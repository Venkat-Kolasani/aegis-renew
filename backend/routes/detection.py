"""Typed detection endpoints with partial-result persistence."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Self

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import Insert as PostgreSQLInsert
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import Insert as SQLiteInsert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.db.connection import get_session_factory
from backend.db.models import Domain
from backend.detection.cert_expiry import (
    CertExpiryResult,
    CertLookupError,
    get_cert_expiry,
)
from backend.detection.domain_expiry import (
    DomainExpiryResult,
    DomainLookupError,
    get_domain_expiry,
    normalize_domain,
)
from backend.detection.takeover_risk import (
    TakeoverLookupError,
    TakeoverRiskResult,
    check_takeover_risk,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["detection"])
_DETAIL_LIMIT = 120


class ScanRequest(BaseModel):
    """Request body for one exact-domain scan."""

    model_config = ConfigDict(extra="forbid")
    domain: str = Field(min_length=1, max_length=253)


class _DomainFactsResponse(BaseModel):
    """Fields shared by the locked domain response contracts."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")
    id: int
    domain: str
    expiry_date: date | None
    cert_expiry_date: datetime | None
    dns_risk: bool

    @field_validator("cert_expiry_date")
    @classmethod
    def cert_expiry_is_utc(
        cls: type[Self], value: datetime | None
    ) -> datetime | None:
        """Return certificate timestamps as timezone-aware UTC."""
        return _as_utc(value)


class DomainResponse(_DomainFactsResponse):
    """Stored-domain response returned by the listing endpoint."""

    last_scanned: datetime

    @field_validator("last_scanned")
    @classmethod
    def last_scanned_is_utc(cls: type[Self], value: datetime) -> datetime:
        """Return scan timestamps as timezone-aware UTC."""
        normalized = _as_utc(value)
        if normalized is None:  # pragma: no cover - model field is required
            raise ValueError("last_scanned is required")
        return normalized


class ScanResponse(_DomainFactsResponse):
    """Persisted combined result returned by the scan endpoint."""

    dns_risk_detail: str | None


@dataclass(frozen=True, slots=True)
class _DetectionSnapshot:
    """Persistable facts produced by three independent detectors."""

    expiry_date: date | None
    cert_expiry_date: datetime | None
    dns_risk: bool
    dns_risk_detail: str | None
    rdap_available: bool
    certificate_available: bool
    takeover_available: bool


def _as_utc(value: datetime | None) -> datetime | None:
    """Normalize a possibly naive database timestamp to aware UTC."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def get_db_session() -> Iterator[Session]:
    """Yield one session from the existing process-wide session factory."""
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


def _log_detector_failure(
    detector: str, domain: str, category: str, error: Exception
) -> None:
    """Log one classified detector failure without provider response data."""
    diagnostic = f"{detector} reported classified {category}"[:_DETAIL_LIMIT]
    logger.warning(
        (
            "Detector failure detector=%s domain=%s category=%s "
            "exception_type=%s diagnostic=%s"
        ),
        detector,
        domain,
        category,
        type(error).__name__,
        diagnostic,
        extra={
            "detector": detector,
            "domain": domain,
            "category": category,
            "exception_type": type(error).__name__,
            "diagnostic": diagnostic,
        },
    )


def _run_rdap(domain: str) -> DomainExpiryResult | None:
    """Run RDAP independently and preserve a classified failure as null."""
    try:
        return get_domain_expiry(domain)
    except DomainLookupError as exc:
        _log_detector_failure("rdap", domain, exc.kind.value, exc)
        return None


def _run_certificate(domain: str) -> CertExpiryResult | None:
    """Run certificate detection independently and preserve failure as null."""
    try:
        return get_cert_expiry(domain)
    except CertLookupError as exc:
        _log_detector_failure("certificate", domain, exc.kind.value, exc)
        return None


def _run_takeover(
    domain: str,
) -> tuple[TakeoverRiskResult | None, str | None]:
    """Run takeover detection and return a sanitized failure category."""
    try:
        return check_takeover_risk(domain), None
    except TakeoverLookupError as exc:
        _log_detector_failure("takeover", domain, exc.kind.value, exc)
        return None, exc.kind.value


def _safe_detail_value(value: str | None) -> str | None:
    """Collapse controls and bound one detector-provided display value."""
    if value is None:
        return None
    printable = "".join(
        character if character.isprintable() else " " for character in value
    )
    collapsed = re.sub(r"\s+", " ", printable).strip()
    return collapsed[:_DETAIL_LIMIT] or None


def _takeover_detail(
    result: TakeoverRiskResult | None, failure_category: str | None
) -> str | None:
    """Format a concise takeover summary without claiming compromise."""
    if result is None:
        if failure_category is None:
            return None
        return (
            f"confidence=unavailable; category={failure_category}; "
            "dns_risk remains false"
        )
    parts = [f"confidence={result.confidence}"]
    target = _safe_detail_value(result.cname_target)
    service = _safe_detail_value(result.matched_service)
    if target is not None:
        parts.append(f"target={target}")
    if service is not None:
        parts.append(f"service={service}")
    if result.confidence == "high":
        parts.append("strong takeover-risk evidence; human review required")
    elif result.confidence == "pattern_only":
        parts.append("uncertain provider pattern; live confirmation inconclusive")
    return "; ".join(parts)


def _run_detectors(domain: str) -> _DetectionSnapshot:
    """Run every detector independently and combine all available facts."""
    rdap = _run_rdap(domain)
    certificate = _run_certificate(domain)
    takeover, takeover_failure = _run_takeover(domain)
    cert_expiry = (
        _as_utc(certificate.not_after) if certificate is not None else None
    )
    return _DetectionSnapshot(
        expiry_date=rdap.expiry_date if rdap is not None else None,
        cert_expiry_date=cert_expiry,
        dns_risk=takeover is not None and takeover.confidence == "high",
        dns_risk_detail=_takeover_detail(takeover, takeover_failure),
        rdap_available=rdap is not None,
        certificate_available=certificate is not None,
        takeover_available=takeover is not None,
    )


def _insert_values(
    domain: str, snapshot: _DetectionSnapshot, scanned_at: datetime
) -> dict[str, object]:
    """Build safe values for a newly inserted domain row."""
    return {
        "domain": domain,
        "expiry_date": snapshot.expiry_date,
        "cert_expiry_date": snapshot.cert_expiry_date,
        "dns_risk": snapshot.dns_risk,
        "dns_risk_detail": snapshot.dns_risk_detail,
        "last_scanned": scanned_at,
    }


def _conflict_update_values(
    snapshot: _DetectionSnapshot, scanned_at: datetime
) -> dict[str, object]:
    """Update only facts whose detector succeeded in the current scan."""
    values: dict[str, object] = {"last_scanned": scanned_at}
    if snapshot.rdap_available:
        values["expiry_date"] = snapshot.expiry_date
    if snapshot.certificate_available:
        values["cert_expiry_date"] = snapshot.cert_expiry_date
    if snapshot.takeover_available:
        values["dns_risk"] = snapshot.dns_risk
        values["dns_risk_detail"] = snapshot.dns_risk_detail
    return values


def _domain_upsert_statement(
    dialect_name: str,
    insert_values: dict[str, object],
    update_values: dict[str, object],
) -> PostgreSQLInsert | SQLiteInsert:
    """Build an atomic domain upsert for PostgreSQL or SQLite."""
    if dialect_name == "postgresql":
        statement = postgresql_insert(Domain).values(**insert_values)
    elif dialect_name == "sqlite":
        statement = sqlite_insert(Domain).values(**insert_values)
    else:
        raise SQLAlchemyError(f"Unsupported database dialect: {dialect_name}")
    return statement.on_conflict_do_update(
        index_elements=[Domain.domain], set_=update_values
    )


def _upsert_domain(
    session: Session, domain: str, snapshot: _DetectionSnapshot
) -> Domain:
    """Atomically insert or update a domain, then load its persisted row."""
    scanned_at = datetime.now(UTC)
    statement = _domain_upsert_statement(
        session.get_bind().dialect.name,
        _insert_values(domain, snapshot, scanned_at),
        _conflict_update_values(snapshot, scanned_at),
    )
    session.execute(statement)
    session.flush()
    query = (
        select(Domain)
        .where(Domain.domain == domain)
        .execution_options(populate_existing=True)
    )
    record = session.scalar(query)
    if record is None:  # pragma: no cover - protected by the atomic statement
        raise SQLAlchemyError("Domain upsert did not produce a row")
    return record


def _database_failure(session: Session, domain: str) -> HTTPException:
    """Roll back and create a non-sensitive persistence error response."""
    session.rollback()
    logger.exception(
        "Detection persistence failed", extra={"domain": domain}
    )
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Detection results could not be stored",
    )


@router.get("/domains", response_model=list[DomainResponse])
def list_domains(session: Session = Depends(get_db_session)) -> list[Domain]:
    """Return stored domain scan summaries in deterministic newest-first order."""
    try:
        statement = select(Domain).order_by(
            Domain.last_scanned.desc(), Domain.id.desc()
        )
        return list(session.scalars(statement))
    except SQLAlchemyError as exc:
        raise _database_failure(session, "<list>") from exc


@router.post("/scan", response_model=ScanResponse)
def scan_domain(
    request: ScanRequest, session: Session = Depends(get_db_session)
) -> Domain:
    """Return persisted partial results from scanning one normalized domain."""
    try:
        domain = normalize_domain(request.domain)
    except DomainLookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Invalid domain",
        ) from exc

    snapshot = _run_detectors(domain)
    try:
        record = _upsert_domain(session, domain, snapshot)
        session.commit()
    except SQLAlchemyError as exc:
        raise _database_failure(session, domain) from exc
    return record
