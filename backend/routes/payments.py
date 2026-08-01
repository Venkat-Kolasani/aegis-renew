"""Prava mandate and payment execution routes."""

from __future__ import annotations

import logging
import re
import threading
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, HttpUrl
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.agent.policy import MandateCoverage, RenewalQuote, apply_renewal_policy
from backend.agent.ranking import DecisionResult
from backend.db.connection import DatabaseConfigurationError, get_session_factory
from backend.db.models import (
    AgentDecision,
    Domain,
    Mandate,
    PaymentAttempt,
    digest_provider_mandate_id,
)
from backend.payments.checkout_adapter import (
    PaymentAuthorizationError,
    on_agent_decision,
    run_demo_mandate_checkout,
)
from backend.payments.demo_constants import (
    DEMO_CURRENCY,
    DEMO_MERCHANT_COUNTRY,
    DEMO_MERCHANT_NAME,
    DEMO_MERCHANT_URL,
    DEMO_RENEWAL_AMOUNT,
)
from backend.payments.demo_merchant import get_demo_renewal_quote
from backend.payments.prava_charge import ProviderMandate, list_provider_mandates
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

    model_config = ConfigDict(extra="forbid", strict=True)

    domain_id: int = Field(gt=0)


class MandateResponse(BaseModel):
    """Contract response for mandate setup."""

    status: str
    approval_url: str


class MandateReconcileRequest(BaseModel):
    """Request body for syncing one domain's approved provider mandate."""

    model_config = ConfigDict(extra="forbid", strict=True)

    domain_id: int = Field(gt=0)


class MandateReconcileResponse(BaseModel):
    """Sanitized result of approved mandate reconciliation."""

    status: Literal["active"]


class PaymentExecutionResponse(BaseModel):
    """Sanitized covered-renewal execution result."""

    payment_status: str
    merchant_order_ref: str | None
    completed: bool


class CoverageDenied(RuntimeError):
    """Raised when current server facts do not authorize renewal execution."""


class QuoteLookupError(RuntimeError):
    """Raised when the server-owned renewal quote is unavailable or malformed."""


class DuplicateExecutionError(RuntimeError):
    """Raised when a prior non-retryable renewal attempt blocks another charge."""


@dataclass(frozen=True, slots=True)
class _ExecutionPrerequisites:
    """Database facts required before any provider access."""

    domain_id: int
    domain: str
    recommendation: DecisionResult


@dataclass(frozen=True, slots=True)
class _AuthorizedExecution:
    """Freshly covered execution facts retained only for this request."""

    domain: str
    recommendation: DecisionResult
    quote: RenewalQuote
    provider_mandate: ProviderMandate
    attempt_id: int


_ORDER_REFERENCE_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_SQLITE_EXECUTION_LOCK = threading.Lock()
_RETRYABLE_ATTEMPT_STATUSES = frozenset(
    {"authorization_failed", "charge_failed", "declined", "declined_report_failed"}
)


@contextmanager
def _payment_session() -> Generator[Session, None, None]:
    """Yield one payment-owned database session without raw error logging."""
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


@contextmanager
def _execution_lock(domain_id: int) -> Generator[None, None, None]:
    """Serialize execution per domain in Postgres and across SQLite tests."""
    session = get_session_factory()()
    dialect = session.get_bind().dialect.name
    if dialect == "sqlite":
        session.close()
        with _SQLITE_EXECUTION_LOCK:
            yield
        return
    if dialect != "postgresql":
        session.close()
        raise DatabaseConfigurationError(
            "Payment execution supports PostgreSQL and SQLite only"
        )
    try:
        session.execute(select(func.pg_advisory_lock(domain_id)))
        yield
    finally:
        try:
            session.execute(select(func.pg_advisory_unlock(domain_id)))
        except SQLAlchemyError as exc:
            logger.error(
                "Payment execution lock release failed exception_type=%s",
                type(exc).__name__,
            )
        session.close()


def _require_domain(domain_id: int) -> Domain:
    """Load a monitored domain or raise an HTTP error."""
    try:
        with _payment_session() as session:
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
        logger.error(
            "Payment database read failed operation=mandate_domain "
            "exception_type=%s",
            type(exc).__name__,
        )
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
    if (
        body.merchant_name.strip() != DEMO_MERCHANT_NAME
        or merchant_url.rstrip("/") != DEMO_MERCHANT_URL
        or country != DEMO_MERCHANT_COUNTRY
        or cap_amount != DEMO_RENEWAL_AMOUNT
        or currency != DEMO_CURRENCY
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Mandate setup must use the fixed DEMO renewal coverage",
        )

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
        logger.error(
            "Prava mandate setup failed category=configuration exception_type=%s",
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Prava is not configured",
        ) from exc
    except PravaMandateError as exc:
        logger.error(
            "Prava mandate setup failed category=provider status_code=%s "
            "exception_type=%s",
            exc.status_code,
            type(exc).__name__,
        )
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


def get_current_execution_quote(
    domain_id: int, observed_at: datetime
) -> RenewalQuote:
    """Return the fresh server-owned DEMO renewal quote for execution."""
    try:
        quote = get_demo_renewal_quote()
        if type(quote.amount) is not Decimal:
            raise TypeError("Quote amount must be Decimal")
        return RenewalQuote(
            domain_id=domain_id,
            merchant_name=quote.merchant_name,
            merchant_url=quote.merchant_url,
            merchant_country=quote.merchant_country,
            amount=quote.amount,
            currency=quote.currency,
            observed_at=observed_at,
        )
    except QuoteLookupError:
        raise
    except Exception as exc:
        logger.error(
            "Renewal quote failed exception_type=%s", type(exc).__name__
        )
        raise QuoteLookupError("Current renewal quote is unavailable") from exc


def _latest_prerequisites(
    session: Session, domain_id: int
) -> _ExecutionPrerequisites:
    """Load the domain and its latest final recommendation."""
    domain = session.get(Domain, domain_id)
    if domain is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Domain not found",
        )
    latest = session.scalar(
        select(AgentDecision)
        .where(AgentDecision.domain_id == domain_id)
        .order_by(AgentDecision.created_at.desc(), AgentDecision.id.desc())
        .limit(1)
    )
    if latest is None or latest.decision != "auto_renew":
        raise CoverageDenied("A final auto-renew decision is required")
    try:
        recommendation = DecisionResult(
            domain_id=domain.id,
            criticality_score=latest.criticality_score,
            decision=latest.decision,
            reason=latest.reason,
        )
    except ValueError as exc:
        raise CoverageDenied("The final decision is invalid") from exc
    return _ExecutionPrerequisites(
        domain_id=domain.id,
        domain=domain.domain,
        recommendation=recommendation,
    )


def _provider_coverage(
    provider: ProviderMandate,
    *,
    domain_id: int,
    evaluated_at: datetime,
    record_id: int,
) -> MandateCoverage:
    """Convert allowlisted provider facts to the deterministic policy shape."""
    return MandateCoverage(
        record_id=record_id,
        domain_id=domain_id,
        merchant_name=provider.merchant_name,
        merchant_url=provider.merchant_url,
        merchant_country=provider.merchant_country,
        cap_amount=provider.cap_amount,
        currency=provider.currency,
        frequency=provider.frequency,
        status=provider.status,
        valid_until=provider.valid_until,
        created_at=evaluated_at,
    )


def _database_time_as_utc(value: datetime | None) -> datetime | None:
    """Normalize timezone-naive SQLite timestamps as stored UTC values."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _persisted_coverage(record: Mandate) -> MandateCoverage:
    """Convert one sanitized mandate row to deterministic policy facts."""
    created_at = _database_time_as_utc(record.created_at)
    if created_at is None:  # pragma: no cover - database column is non-nullable
        raise SQLAlchemyError("Mandate creation timestamp was missing")
    return MandateCoverage(
        record_id=record.id,
        domain_id=record.domain_id,
        merchant_name=record.merchant_name,
        merchant_url=record.merchant_url,
        merchant_country=record.merchant_country,
        cap_amount=record.cap_amount,
        currency=record.currency,
        frequency=record.frequency,
        status=record.status,
        valid_until=_database_time_as_utc(record.valid_until),
        created_at=created_at,
    )


def _covered_persisted_mandates(
    session: Session,
    *,
    prerequisites: _ExecutionPrerequisites,
    quote: RenewalQuote,
    evaluated_at: datetime,
) -> list[Mandate]:
    """Return persisted mandates independently covering the fresh quote."""
    records = list(
        session.scalars(
            select(Mandate)
            .where(Mandate.domain_id == prerequisites.domain_id)
            .order_by(Mandate.created_at.desc(), Mandate.id.desc())
        )
    )
    return [
        record
        for record in records
        if apply_renewal_policy(
            prerequisites.recommendation,
            quote=quote,
            mandates=(_persisted_coverage(record),),
            evaluated_at=evaluated_at,
        ).decision
        == "auto_renew"
    ]


def _reject_duplicate_attempt(session: Session, domain_id: int) -> None:
    """Block another charge after any non-retryable attempt for the domain."""
    attempt_id = session.scalar(
        select(PaymentAttempt.id)
        .where(
            PaymentAttempt.domain_id == domain_id,
            PaymentAttempt.status.not_in(_RETRYABLE_ATTEMPT_STATUSES),
        )
        .limit(1)
    )
    if attempt_id is not None:
        raise DuplicateExecutionError("A renewal execution is already recorded")


def _require_persisted_preflight(
    *,
    domain_id: int,
    quote: RenewalQuote,
    evaluated_at: datetime,
) -> _ExecutionPrerequisites:
    """Fail closed on local coverage before contacting the provider."""
    with _payment_session() as session:
        prerequisites = _latest_prerequisites(session, domain_id)
        _reject_duplicate_attempt(session, domain_id)
        covered = _covered_persisted_mandates(
            session,
            prerequisites=prerequisites,
            quote=quote,
            evaluated_at=evaluated_at,
        )
        if not covered:
            raise CoverageDenied("No persisted mandate covers this renewal")
        return prerequisites


def _covered_provider_mandates(
    providers: Sequence[ProviderMandate],
    *,
    prerequisites: _ExecutionPrerequisites,
    quote: RenewalQuote,
    evaluated_at: datetime,
) -> list[ProviderMandate]:
    """Return provider mandates that each independently cover the fresh quote."""
    expected_customer_id = f"aegis_domain_{prerequisites.domain_id}"
    covered: list[ProviderMandate] = []
    for index, provider in enumerate(providers, start=1):
        if provider.customer_id not in {None, expected_customer_id}:
            continue
        result = apply_renewal_policy(
            prerequisites.recommendation,
            quote=quote,
            mandates=(
                _provider_coverage(
                    provider,
                    domain_id=prerequisites.domain_id,
                    evaluated_at=evaluated_at,
                    record_id=index,
                ),
            ),
            evaluated_at=evaluated_at,
        )
        if result.decision == "auto_renew":
            covered.append(provider)
    return sorted(
        covered,
        key=lambda item: (
            item.valid_until,
            digest_provider_mandate_id(item.provider_id),
        ),
        reverse=True,
    )


def _provider_mandates_or_http(domain_id: int) -> list[ProviderMandate]:
    """Load one domain's provider mandates or raise a sanitized HTTP error."""
    try:
        return list_provider_mandates(customer_id=f"aegis_domain_{domain_id}")
    except PravaConfigurationError as exc:
        logger.error(
            "Prava mandate lookup failed category=configuration "
            "exception_type=%s",
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Prava is not configured",
        ) from exc
    except PravaMandateError as exc:
        logger.error(
            "Prava mandate lookup failed category=provider exception_type=%s",
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Prava mandate lookup failed",
        ) from exc


def _reconcile_mandate(
    session: Session,
    *,
    domain_id: int,
    provider: ProviderMandate,
) -> Mandate:
    """Persist only the one-way digest and sanitized provider mandate facts."""
    provider_digest = digest_provider_mandate_id(provider.provider_id)
    mandate = session.scalar(
        select(Mandate).where(
            Mandate.provider_mandate_id_digest == provider_digest
        )
    )
    if mandate is not None and mandate.domain_id != domain_id:
        raise CoverageDenied("The provider mandate is bound to another domain")
    if mandate is None:
        mandate = Mandate(
            domain_id=domain_id,
            provider_mandate_id_digest=provider_digest,
            merchant_name=provider.merchant_name,
            merchant_url=provider.merchant_url,
            merchant_country=provider.merchant_country,
            cap_amount=provider.cap_amount,
            currency=provider.currency,
            frequency=provider.frequency,
            status=provider.status,
            valid_until=provider.valid_until,
        )
        session.add(mandate)
    else:
        mandate.merchant_name = provider.merchant_name
        mandate.merchant_url = provider.merchant_url
        mandate.merchant_country = provider.merchant_country
        mandate.cap_amount = provider.cap_amount
        mandate.currency = provider.currency
        mandate.frequency = provider.frequency
        mandate.status = provider.status
        mandate.valid_until = provider.valid_until
    session.flush()
    return mandate


@router.post(
    "/payments/mandate/reconcile",
    response_model=MandateReconcileResponse,
)
def reconcile_mandate(body: MandateReconcileRequest) -> MandateReconcileResponse:
    """Reconcile an approved mandate and return its sanitized active status."""
    domain = _require_domain(body.domain_id)
    evaluated_at = datetime.now(UTC)
    try:
        quote = get_current_execution_quote(body.domain_id, evaluated_at)
    except QuoteLookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Current renewal quote is unavailable",
        ) from exc
    providers = _provider_mandates_or_http(body.domain_id)
    prerequisites = _ExecutionPrerequisites(
        domain_id=domain.id,
        domain=domain.domain,
        recommendation=DecisionResult(
            domain_id=domain.id,
            criticality_score=0,
            decision="auto_renew",
            reason="Mandate metadata reconciliation only.",
        ),
    )
    covered = _covered_provider_mandates(
        providers,
        prerequisites=prerequisites,
        quote=quote,
        evaluated_at=evaluated_at,
    )
    if not covered:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No approved mandate covers the current renewal",
        )
    try:
        with _payment_session() as session:
            _reconcile_mandate(
                session,
                domain_id=body.domain_id,
                provider=covered[0],
            )
            session.commit()
    except (DatabaseConfigurationError, SQLAlchemyError) as exc:
        raise _database_http_error(exc) from exc
    return MandateReconcileResponse(status="active")


def _authorize_attempt(
    *,
    domain_id: int,
    providers: Sequence[ProviderMandate],
    quote: RenewalQuote,
    evaluated_at: datetime,
) -> _AuthorizedExecution:
    """Re-check coverage, reconcile the mandate digest, and persist one attempt."""
    with _payment_session() as session:
        prerequisites = _latest_prerequisites(session, domain_id)
        _reject_duplicate_attempt(session, domain_id)
        persisted = _covered_persisted_mandates(
            session,
            prerequisites=prerequisites,
            quote=quote,
            evaluated_at=evaluated_at,
        )
        if not persisted:
            raise CoverageDenied("No persisted mandate covers this renewal")
        by_digest = {
            record.provider_mandate_id_digest: record for record in persisted
        }
        provider_matches = _covered_provider_mandates(
            providers,
            prerequisites=prerequisites,
            quote=quote,
            evaluated_at=evaluated_at,
        )
        covered = [
            provider
            for provider in provider_matches
            if digest_provider_mandate_id(provider.provider_id) in by_digest
        ]
        if not covered:
            raise CoverageDenied("No current provider mandate covers this renewal")
        provider = covered[0]
        mandate = _reconcile_mandate(
            session,
            domain_id=domain_id,
            provider=provider,
        )
        attempt = PaymentAttempt(
            domain_id=domain_id,
            mandate_id=mandate.id,
            amount=quote.amount,
            merchant_order_ref=None,
            status="authorized",
        )
        session.add(attempt)
        session.flush()
        attempt_id = attempt.id
        session.commit()
    return _AuthorizedExecution(
        domain=prerequisites.domain,
        recommendation=prerequisites.recommendation,
        quote=quote,
        provider_mandate=provider,
        attempt_id=attempt_id,
    )


def _update_attempt(
    attempt_id: int,
    *,
    payment_status: str,
    merchant_order_ref: str | None,
) -> None:
    """Persist one sanitized final attempt state."""
    if merchant_order_ref is not None and not _ORDER_REFERENCE_PATTERN.fullmatch(
        merchant_order_ref
    ):
        raise SQLAlchemyError("Merchant order reference was invalid")
    with _payment_session() as session:
        attempt = session.get(PaymentAttempt, attempt_id)
        if attempt is None:
            raise SQLAlchemyError("Authorized payment attempt was not found")
        attempt.status = payment_status
        attempt.merchant_order_ref = merchant_order_ref
        session.commit()


def _mark_attempt_failure(attempt_id: int, payment_status: str) -> None:
    """Best-effort persist a sanitized failed provider stage."""
    try:
        _update_attempt(
            attempt_id,
            payment_status=payment_status,
            merchant_order_ref=None,
        )
    except (DatabaseConfigurationError, SQLAlchemyError) as exc:
        logger.error(
            "Payment failure-state persistence failed exception_type=%s",
            type(exc).__name__,
        )


def _database_http_error(exc: Exception) -> HTTPException:
    """Return the sanitized payment database failure contract."""
    logger.error(
        "Payment database operation failed exception_type=%s",
        type(exc).__name__,
    )
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Payment data is unavailable",
    )


def _execute_payment_locked(domain_id: int) -> PaymentExecutionResponse:
    """Execute one renewal while the domain-specific spending lock is held."""
    try:
        with _payment_session() as session:
            _latest_prerequisites(session, domain_id)
    except (DatabaseConfigurationError, SQLAlchemyError) as exc:
        raise _database_http_error(exc) from exc
    except CoverageDenied as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Renewal execution is not currently authorized",
        ) from exc

    evaluated_at = datetime.now(UTC)
    try:
        quote = get_current_execution_quote(domain_id, evaluated_at)
    except QuoteLookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Current renewal quote is unavailable",
        ) from exc

    try:
        prerequisites = _require_persisted_preflight(
            domain_id=domain_id,
            quote=quote,
            evaluated_at=evaluated_at,
        )
    except DuplicateExecutionError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Renewal execution is already recorded",
        ) from exc
    except CoverageDenied as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Renewal is not covered by an active mandate",
        ) from exc
    except (DatabaseConfigurationError, SQLAlchemyError) as exc:
        raise _database_http_error(exc) from exc

    providers = _provider_mandates_or_http(prerequisites.domain_id)

    try:
        authorization = _authorize_attempt(
            domain_id=domain_id,
            providers=providers,
            quote=quote,
            evaluated_at=evaluated_at,
        )
    except (CoverageDenied, DuplicateExecutionError) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Renewal is not covered by an active mandate",
        ) from exc
    except (DatabaseConfigurationError, SQLAlchemyError) as exc:
        raise _database_http_error(exc) from exc

    try:
        on_agent_decision(
            authorization.domain,
            authorization.recommendation.decision,
            float(authorization.quote.amount),
            authorization.recommendation.reason,
        )
    except PaymentAuthorizationError as exc:
        _mark_attempt_failure(authorization.attempt_id, "authorization_failed")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Renewal execution is not currently authorized",
        ) from exc

    try:
        outcome = run_demo_mandate_checkout(
            domain=authorization.domain,
            provider_mandate=authorization.provider_mandate,
            quote=authorization.quote,
        )
    except PravaConfigurationError as exc:
        _mark_attempt_failure(authorization.attempt_id, "charge_failed")
        logger.error(
            "Prava charge failed category=configuration exception_type=%s",
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Prava is not configured",
        ) from exc
    except PravaMandateError as exc:
        _mark_attempt_failure(authorization.attempt_id, "charge_failed")
        logger.error(
            "Prava charge failed category=provider exception_type=%s",
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Prava mandate charge failed",
        ) from exc

    try:
        _update_attempt(
            authorization.attempt_id,
            payment_status=outcome.payment_status,
            merchant_order_ref=outcome.merchant_order_ref,
        )
    except (DatabaseConfigurationError, SQLAlchemyError) as exc:
        raise _database_http_error(exc) from exc
    return PaymentExecutionResponse(
        payment_status=outcome.payment_status,
        merchant_order_ref=outcome.merchant_order_ref,
        completed=outcome.completed,
    )


@router.post("/payments/execute", response_model=PaymentExecutionResponse)
def execute_payment(body: ExecuteRequest) -> PaymentExecutionResponse:
    """Execute a freshly covered renewal and return its sanitized outcome."""
    try:
        with _execution_lock(body.domain_id):
            return _execute_payment_locked(body.domain_id)
    except (DatabaseConfigurationError, SQLAlchemyError) as exc:
        raise _database_http_error(exc) from exc


class DemoQuoteResponse(BaseModel):
    """DEMO: Fixed registrar renewal quote."""

    merchant_name: str
    merchant_url: str
    merchant_country: str
    product_description: str
    amount: str
    currency: str


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
