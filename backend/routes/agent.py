"""Deterministic, non-spending domain-ranking endpoint."""

from __future__ import annotations

import logging
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Self

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.agent.policy import MandateCoverage, RenewalQuote, apply_renewal_policy
from backend.agent.ranking import (
    MAX_REQUEST_ITEMS,
    DecisionResult,
    RankingError,
    rank_domains,
)
from backend.db.connection import get_session_factory
from backend.db.models import AgentDecision, Domain, Mandate
from backend.payments.demo_merchant import get_demo_renewal_quote

logger = logging.getLogger(__name__)
router = APIRouter(tags=["agent"])


class RankRequest(BaseModel):
    """Request body for ranking distinct scanned domains."""

    model_config = ConfigDict(extra="forbid", strict=True)

    domain_ids: list[int]

    @field_validator("domain_ids", mode="before")
    @classmethod
    def validate_domain_ids(cls: type[Self], value: object) -> list[int]:
        """Require a bounded non-empty list of distinct positive integers."""
        if not isinstance(value, list) or not value:
            raise ValueError("domain_ids must be a non-empty list")
        if len(value) > MAX_REQUEST_ITEMS:
            raise ValueError("domain_ids exceeds the request item limit")
        if any(type(item) is not int or item <= 0 for item in value):
            raise ValueError("domain_ids must contain positive integers")
        if len(value) != len(set(value)):
            raise ValueError("domain_ids must not contain duplicates")
        return value


class QuoteProviderError(RuntimeError):
    """Raised when the read-only server quote cannot be represented safely."""


def get_db_session() -> Iterator[Session]:
    """Yield one session from the existing process-wide session factory."""
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


def get_current_renewal_quote(
    domain_id: int, observed_at: datetime
) -> RenewalQuote:
    """Return the server-derived DEMO registrar quote for one domain."""
    # DEMO: this adapts the disclosed fixed registrar quote without executing it.
    try:
        demo_quote = get_demo_renewal_quote()
        if type(demo_quote.amount) is not Decimal:
            raise TypeError("DEMO quote amount must be Decimal")
        return RenewalQuote(
            domain_id=domain_id,
            merchant_name=demo_quote.merchant_name,
            merchant_url=demo_quote.merchant_url,
            merchant_country=demo_quote.merchant_country,
            amount=demo_quote.amount,
            currency=demo_quote.currency,
            observed_at=observed_at,
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise QuoteProviderError("Current renewal quote is unavailable") from exc


def _require_existing_domains(session: Session, domain_ids: Sequence[int]) -> None:
    """Require every requested domain ID to exist before ranking."""
    existing = {
        domain.id
        for domain in session.scalars(
            select(Domain).where(Domain.id.in_(domain_ids))
        )
    }
    if existing != set(domain_ids):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="One or more domains were not found",
        )


def _rank_once(domain_ids: list[int]) -> list[DecisionResult]:
    """Run ranking once and reject an unexpected internal result contract."""
    try:
        recommendations = rank_domains(domain_ids)
    except RankingError as exc:
        logger.warning(
            "Ranking failed category=%s exception_type=%s",
            exc.kind.value,
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Automated ranking is unavailable",
        ) from exc
    if [item.domain_id for item in recommendations] != domain_ids:
        logger.error("Ranking returned an invalid domain ID contract")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Automated ranking is unavailable",
        )
    return recommendations


def _quotes_for_domains(
    domain_ids: Sequence[int], evaluated_at: datetime
) -> dict[int, RenewalQuote | None]:
    """Load independent current quotes and fail closed per domain."""
    quotes: dict[int, RenewalQuote | None] = {}
    for domain_id in domain_ids:
        try:
            quote = get_current_renewal_quote(domain_id, evaluated_at)
            if not isinstance(quote, RenewalQuote):
                raise QuoteProviderError("Malformed quote")
            quotes[domain_id] = quote
        except QuoteProviderError as exc:
            logger.warning(
                "Quote unavailable domain_id=%s exception_type=%s",
                domain_id,
                type(exc).__name__,
            )
            quotes[domain_id] = None
    return quotes


def _load_mandate_coverage(
    session: Session, domain_ids: Sequence[int]
) -> dict[int, list[MandateCoverage]]:
    """Load only sanitized mandate fields required by deterministic policy."""
    statement = (
        select(
            Mandate.id,
            Mandate.domain_id,
            Mandate.merchant_name,
            Mandate.merchant_url,
            Mandate.merchant_country,
            Mandate.cap_amount,
            Mandate.currency,
            Mandate.frequency,
            Mandate.status,
            Mandate.valid_until,
            Mandate.created_at,
        )
        .where(Mandate.domain_id.in_(domain_ids))
        .order_by(
            Mandate.domain_id,
            Mandate.created_at.desc(),
            Mandate.id.desc(),
        )
    )
    coverage: dict[int, list[MandateCoverage]] = {}
    for row in session.execute(statement):
        item = _coverage_from_row(row)
        coverage.setdefault(item.domain_id, []).append(item)
    return coverage


def _coverage_from_row(row: object) -> MandateCoverage:
    """Convert one selected database row into sanitized immutable coverage."""
    return MandateCoverage(
        record_id=row.id,  # type: ignore[attr-defined]
        domain_id=row.domain_id,  # type: ignore[attr-defined]
        merchant_name=row.merchant_name,  # type: ignore[attr-defined]
        merchant_url=row.merchant_url,  # type: ignore[attr-defined]
        merchant_country=row.merchant_country,  # type: ignore[attr-defined]
        cap_amount=row.cap_amount,  # type: ignore[attr-defined]
        currency=row.currency,  # type: ignore[attr-defined]
        frequency=row.frequency,  # type: ignore[attr-defined]
        status=row.status,  # type: ignore[attr-defined]
        valid_until=_database_time_as_utc(row.valid_until),  # type: ignore[attr-defined]
        created_at=_required_database_time(row.created_at),  # type: ignore[attr-defined]
    )


def _database_time_as_utc(value: datetime | None) -> datetime | None:
    """Normalize timezone-naive SQLite values as stored UTC timestamps."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _required_database_time(value: datetime) -> datetime:
    """Return a normalized required database timestamp."""
    normalized = _database_time_as_utc(value)
    if normalized is None:  # pragma: no cover - ORM field is non-nullable
        raise SQLAlchemyError("Required mandate timestamp was missing")
    return normalized


def _apply_policy_to_all(
    recommendations: Sequence[DecisionResult],
    quotes: dict[int, RenewalQuote | None],
    mandates: dict[int, list[MandateCoverage]],
    evaluated_at: datetime,
) -> list[DecisionResult]:
    """Apply independent deterministic coverage to every recommendation."""
    return [
        apply_renewal_policy(
            recommendation,
            quote=quotes.get(recommendation.domain_id),
            mandates=mandates.get(recommendation.domain_id, ()),
            evaluated_at=evaluated_at,
        )
        for recommendation in recommendations
    ]


def _persist_final_decisions(
    session: Session, decisions: Sequence[DecisionResult]
) -> None:
    """Persist only final post-policy sanitized recommendation fields."""
    session.add_all(
        [
            AgentDecision(
                domain_id=item.domain_id,
                criticality_score=item.criticality_score,
                decision=item.decision,
                reason=item.reason,
            )
            for item in decisions
        ]
    )
    session.flush()
    session.commit()


def _database_failure(
    session: Session, operation: str, error: SQLAlchemyError
) -> HTTPException:
    """Roll back and return one sanitized database failure response."""
    session.rollback()
    logger.error(
        "Agent database failure operation=%s exception_type=%s",
        operation,
        type(error).__name__,
    )
    detail = (
        "Ranking decisions could not be stored"
        if operation == "persist"
        else "Ranking data could not be loaded"
    )
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=detail,
    )


@router.post("/agent/rank", response_model=list[DecisionResult])
def rank_requested_domains(
    request: RankRequest,
    session: Session = Depends(get_db_session),
) -> list[DecisionResult]:
    """Return and persist final post-policy recommendations for scanned domains."""
    try:
        _require_existing_domains(session, request.domain_ids)
    except SQLAlchemyError as exc:
        raise _database_failure(session, "read_domains", exc) from exc

    recommendations = _rank_once(request.domain_ids)
    evaluated_at = datetime.now(UTC)
    quotes = _quotes_for_domains(request.domain_ids, evaluated_at)
    try:
        mandates = _load_mandate_coverage(session, request.domain_ids)
    except SQLAlchemyError as exc:
        raise _database_failure(session, "read_mandates", exc) from exc

    final_decisions = _apply_policy_to_all(
        recommendations,
        quotes,
        mandates,
        evaluated_at,
    )
    try:
        _persist_final_decisions(session, final_decisions)
    except SQLAlchemyError as exc:
        raise _database_failure(session, "persist", exc) from exc
    return final_decisions
