"""SQLAlchemy ORM models for the approved Aegis database schema."""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CHAR,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    Text,
    false,
    func,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
    validates,
)

SQLITE_COMPATIBLE_BIGINT = BigInteger().with_variant(Integer, "sqlite")
MANDATE_DIGEST_PREFIX = "sha256:"
MANDATE_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


class Base(DeclarativeBase):
    """Base class for all Aegis ORM models."""


def digest_provider_mandate_id(provider_mandate_id: str) -> str:
    """Return a one-way, domain-separated digest of a provider mandate ID."""
    normalized_id = provider_mandate_id.strip()
    if not normalized_id:
        raise ValueError("Provider mandate ID cannot be empty")
    digest = hashlib.sha256(
        f"aegis:provider-mandate:v1:{normalized_id}".encode()
    ).hexdigest()
    return f"{MANDATE_DIGEST_PREFIX}{digest}"


class Domain(Base):
    """A monitored domain and its latest persisted scan results."""

    __tablename__ = "domains"

    id: Mapped[int] = mapped_column(SQLITE_COMPATIBLE_BIGINT, primary_key=True)
    domain: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    expiry_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    cert_expiry_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    dns_risk: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=false()
    )
    dns_risk_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_scanned: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    decisions: Mapped[list[AgentDecision]] = relationship(
        back_populates="domain", cascade="all, delete-orphan", passive_deletes=True
    )
    mandates: Mapped[list[Mandate]] = relationship(
        back_populates="domain", cascade="all, delete-orphan", passive_deletes=True
    )
    payment_attempts: Mapped[list[PaymentAttempt]] = relationship(
        back_populates="domain", cascade="all, delete-orphan", passive_deletes=True
    )


class AgentDecision(Base):
    """A persisted, non-spending ranking recommendation for a domain."""

    __tablename__ = "agent_decisions"
    __table_args__ = (
        CheckConstraint(
            "criticality_score BETWEEN 0 AND 100", name="ck_agent_decisions_score"
        ),
        CheckConstraint(
            "decision IN ('auto_renew', 'flag_for_review', 'ignore')",
            name="ck_agent_decisions_decision",
        ),
    )

    id: Mapped[int] = mapped_column(SQLITE_COMPATIBLE_BIGINT, primary_key=True)
    domain_id: Mapped[int] = mapped_column(
        SQLITE_COMPATIBLE_BIGINT,
        ForeignKey("domains.id", ondelete="CASCADE"),
        nullable=False,
    )
    criticality_score: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    decision: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    domain: Mapped[Domain] = relationship(back_populates="decisions")


class Mandate(Base):
    """Sanitized mandate metadata bound to one domain and merchant."""

    __tablename__ = "mandates"
    __table_args__ = (
        CheckConstraint("cap_amount > 0", name="ck_mandates_cap_amount"),
        CheckConstraint("frequency = 'yearly'", name="ck_mandates_frequency"),
    )

    id: Mapped[int] = mapped_column(SQLITE_COMPATIBLE_BIGINT, primary_key=True)
    domain_id: Mapped[int] = mapped_column(
        SQLITE_COMPATIBLE_BIGINT,
        ForeignKey("domains.id", ondelete="CASCADE"),
        nullable=False,
    )
    # SECURITY: the shared SQL column is unfortunately named provider_mandate_id.
    # The ORM exposes only a validated one-way digest so raw identifiers cannot
    # be persisted through this model. Renaming the SQL column requires handoff.
    provider_mandate_id_digest: Mapped[str] = mapped_column(
        "provider_mandate_id", Text, nullable=False, unique=True
    )
    merchant_name: Mapped[str] = mapped_column(Text, nullable=False)
    merchant_url: Mapped[str] = mapped_column(Text, nullable=False)
    merchant_country: Mapped[str] = mapped_column(CHAR(2), nullable=False)
    cap_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    frequency: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    valid_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    domain: Mapped[Domain] = relationship(back_populates="mandates")
    payment_attempts: Mapped[list[PaymentAttempt]] = relationship(
        back_populates="mandate", passive_deletes=True
    )

    @validates("provider_mandate_id_digest")
    def validate_provider_mandate_digest(self, _: str, value: str) -> str:
        """Reject raw provider identifiers and accept only SHA-256 digests."""
        if not MANDATE_DIGEST_PATTERN.fullmatch(value):
            raise ValueError("Only a provider mandate ID digest may be persisted")
        return value


class PaymentAttempt(Base):
    """A sanitized payment outcome without payment credentials or secrets."""

    __tablename__ = "payment_attempts"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_payment_attempts_amount"),
    )

    id: Mapped[int] = mapped_column(SQLITE_COMPATIBLE_BIGINT, primary_key=True)
    domain_id: Mapped[int] = mapped_column(
        SQLITE_COMPATIBLE_BIGINT,
        ForeignKey("domains.id", ondelete="CASCADE"),
        nullable=False,
    )
    mandate_id: Mapped[int] = mapped_column(
        SQLITE_COMPATIBLE_BIGINT,
        ForeignKey("mandates.id", ondelete="RESTRICT"),
        nullable=False,
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    merchant_order_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    domain: Mapped[Domain] = relationship(back_populates="payment_attempts")
    mandate: Mapped[Mandate] = relationship(back_populates="payment_attempts")
