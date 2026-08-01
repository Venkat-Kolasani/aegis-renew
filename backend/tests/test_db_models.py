"""Persistence tests for the Aegis SQLAlchemy models."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from backend.db.models import AgentDecision, Domain, Mandate, digest_provider_mandate_id


def test_domain_can_be_inserted_and_queried(db_session: Session) -> None:
    """A domain round-trips through the isolated database."""
    domain = Domain(domain="example.com", expiry_date=date(2027, 8, 1))
    db_session.add(domain)
    db_session.commit()

    stored_domain = db_session.scalar(
        select(Domain).where(Domain.domain == "example.com")
    )

    assert stored_domain is not None
    assert stored_domain.id is not None
    assert stored_domain.expiry_date == date(2027, 8, 1)
    assert stored_domain.dns_risk is False
    assert stored_domain.last_scanned is not None


def test_decision_can_be_inserted_queried_and_related(db_session: Session) -> None:
    """A decision round-trips and remains related to its domain."""
    domain = Domain(domain="renew.example")
    domain.decisions.append(
        AgentDecision(
            criticality_score=92,
            decision="flag_for_review",
            reason="Domain expiry is near and requires coverage review.",
        )
    )
    db_session.add(domain)
    db_session.commit()
    db_session.expunge_all()

    stored_decision = db_session.scalar(
        select(AgentDecision).options(selectinload(AgentDecision.domain))
    )

    assert stored_decision is not None
    assert stored_decision.criticality_score == 92
    assert stored_decision.domain.domain == "renew.example"
    assert stored_decision in stored_decision.domain.decisions


def test_mandate_model_rejects_raw_provider_identifier() -> None:
    """The ORM cannot persist a raw provider mandate identifier."""
    with pytest.raises(ValueError, match="digest"):
        Mandate(
            domain_id=1,
            provider_mandate_id_digest="raw-provider-mandate-id",
            merchant_name="Registrar",
            merchant_url="https://registrar.example",
            merchant_country="US",
            cap_amount=Decimal("25.00"),
            currency="USD",
            frequency="yearly",
            status="active",
        )


def test_provider_identifier_digest_does_not_contain_raw_value() -> None:
    """The helper converts a raw provider ID to a one-way stored value."""
    raw_identifier = "provider-mandate-sensitive-value"

    digest = digest_provider_mandate_id(raw_identifier)

    assert digest.startswith("sha256:")
    assert raw_identifier not in digest
