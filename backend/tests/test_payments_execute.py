"""Offline integration tests for covered POST /api/payments/execute."""

from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.agent.policy import RenewalQuote
from backend.db import connection as db_connection
from backend.db.connection import create_database_engine, create_session_factory
from backend.db.models import (
    AgentDecision,
    Base,
    Domain,
    Mandate,
    PaymentAttempt,
    digest_provider_mandate_id,
)
from backend.main import create_app
from backend.payments.checkout_adapter import DemoRenewalCheckoutOutcome
from backend.payments.demo_constants import (
    DEMO_CURRENCY,
    DEMO_MERCHANT_COUNTRY,
    DEMO_MERCHANT_NAME,
    DEMO_MERCHANT_URL,
    DEMO_RENEWAL_AMOUNT,
)
from backend.payments.prava_charge import ProviderMandate
from backend.payments.prava_mandate import PravaMandateError
from backend.routes import payments as payment_routes

NOW = datetime.now(UTC)
RAW_PROVIDER_ID = "mdt_sensitive_test_identifier"


@pytest.fixture()
def payment_db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Session]:
    """Wire the payment route to one isolated SQLite database."""
    database_url = f"sqlite:///{tmp_path / 'execute.db'}"
    engine = create_database_engine(database_url)
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    monkeypatch.setattr(db_connection, "_engine", engine)
    monkeypatch.setattr(db_connection, "_session_factory", factory)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()
        monkeypatch.setattr(db_connection, "_engine", None)
        monkeypatch.setattr(db_connection, "_session_factory", None)


@pytest.fixture()
def client() -> TestClient:
    """Return the application client for payment integration tests."""
    return TestClient(create_app())


@pytest.fixture(autouse=True)
def payment_boundaries(monkeypatch: pytest.MonkeyPatch) -> dict[str, Mock]:
    """Keep provider, checkout, and decision boundaries fully offline."""
    provider = Mock(return_value=[_provider()])
    checkout = Mock(return_value=_outcome())
    decision_hook = Mock()
    monkeypatch.setattr(payment_routes, "list_provider_mandates", provider)
    monkeypatch.setattr(payment_routes, "run_demo_mandate_checkout", checkout)
    monkeypatch.setattr(payment_routes, "on_agent_decision", decision_hook)
    return {
        "provider": provider,
        "checkout": checkout,
        "decision_hook": decision_hook,
    }


def _provider(**changes: object) -> ProviderMandate:
    """Build one active provider mandate matching the server quote."""
    item = ProviderMandate(
        provider_id=RAW_PROVIDER_ID,
        customer_id=None,
        merchant_name=DEMO_MERCHANT_NAME,
        merchant_url=DEMO_MERCHANT_URL,
        merchant_country=DEMO_MERCHANT_COUNTRY,
        cap_amount=Decimal("25.00"),
        currency=DEMO_CURRENCY,
        frequency="yearly",
        status="active",
        valid_until=NOW + timedelta(days=365),
    )
    return replace(item, **changes)


def _outcome(**changes: object) -> DemoRenewalCheckoutOutcome:
    """Build one sanitized successful DEMO checkout result."""
    item = DemoRenewalCheckoutOutcome(
        completed=True,
        payment_status="completed",
        merchant_order_ref="DEMO-REN-20260802-ABC12345",
        amount=DEMO_RENEWAL_AMOUNT,
        currency=DEMO_CURRENCY,
        prava_report_status="completed",
        detail="DEMO checkout completed",
    )
    return replace(item, **changes)


def _add_domain(session: Session, name: str = "billing.aegis-demo.test") -> Domain:
    """Persist one monitored domain."""
    domain = Domain(domain=name, last_scanned=NOW)
    session.add(domain)
    session.commit()
    session.refresh(domain)
    return domain


def _add_decision(
    session: Session,
    domain_id: int,
    *,
    decision: str = "auto_renew",
) -> AgentDecision:
    """Persist one final ranking decision."""
    item = AgentDecision(
        domain_id=domain_id,
        criticality_score=91,
        decision=decision,
        reason="Domain renewal is urgent.",
        created_at=NOW,
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


def _add_mandate(
    session: Session,
    domain_id: int,
    **provider_changes: object,
) -> Mandate:
    """Persist one sanitized mandate matching an ephemeral provider record."""
    provider = _provider(**provider_changes)
    mandate = Mandate(
        domain_id=domain_id,
        provider_mandate_id_digest=digest_provider_mandate_id(provider.provider_id),
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
    session.commit()
    session.refresh(mandate)
    return mandate


def _attempt_count(session: Session) -> int:
    """Return the number of persisted payment attempts."""
    session.expire_all()
    return session.scalar(select(func.count()).select_from(PaymentAttempt)) or 0


def test_execute_success_reconciles_digest_and_persists_sanitized_attempt(
    client: TestClient,
    payment_db: Session,
    payment_boundaries: dict[str, Mock],
) -> None:
    """The covered route stores only mandate metadata and sanitized outcome."""
    domain = _add_domain(payment_db)
    _add_decision(payment_db, domain.id)
    _add_mandate(payment_db, domain.id)

    response = client.post("/api/payments/execute", json={"domain_id": domain.id})

    assert response.status_code == 200
    assert response.json() == {
        "payment_status": "completed",
        "merchant_order_ref": "DEMO-REN-20260802-ABC12345",
        "completed": True,
    }
    mandate = payment_db.scalars(select(Mandate)).one()
    assert mandate.provider_mandate_id_digest == digest_provider_mandate_id(
        RAW_PROVIDER_ID
    )
    assert RAW_PROVIDER_ID not in mandate.provider_mandate_id_digest
    attempt = payment_db.scalars(select(PaymentAttempt)).one()
    assert attempt.domain_id == domain.id
    assert attempt.mandate_id == mandate.id
    assert attempt.amount == Decimal("18.00")
    assert attempt.status == "completed"
    assert attempt.merchant_order_ref == "DEMO-REN-20260802-ABC12345"
    payment_boundaries["provider"].assert_called_once_with(
        customer_id=f"aegis_domain_{domain.id}"
    )
    payment_boundaries["decision_hook"].assert_called_once()
    payment_boundaries["checkout"].assert_called_once()


def test_concurrent_duplicate_execute_cannot_make_a_second_charge(
    payment_db: Session,
    payment_boundaries: dict[str, Mock],
) -> None:
    """The execution lock and persisted guard permit only one charge."""
    domain = _add_domain(payment_db)
    _add_decision(payment_db, domain.id)
    _add_mandate(payment_db, domain.id)

    def execute() -> tuple[int, dict[str, object]]:
        with TestClient(create_app()) as request_client:
            response = request_client.post(
                "/api/payments/execute", json={"domain_id": domain.id}
            )
            return response.status_code, response.json()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: execute(), range(2)))

    assert sorted(status_code for status_code, _ in results) == [200, 409]
    assert (409, {"detail": "Renewal execution is already recorded"}) in results
    payment_boundaries["provider"].assert_called_once()
    payment_boundaries["checkout"].assert_called_once()
    assert _attempt_count(payment_db) == 1


def test_post_approval_reconciliation_bootstraps_policy_without_an_attempt(
    client: TestClient,
    payment_db: Session,
    payment_boundaries: dict[str, Mock],
) -> None:
    """A domain-only sync stores active coverage before ranking can authorize."""
    domain = _add_domain(payment_db)

    sync = client.post(
        "/api/payments/mandate/reconcile",
        json={"domain_id": domain.id},
    )

    assert sync.status_code == 200
    assert sync.json() == {"status": "active"}
    mandate = payment_db.scalars(select(Mandate)).one()
    assert mandate.provider_mandate_id_digest == digest_provider_mandate_id(
        RAW_PROVIDER_ID
    )
    assert _attempt_count(payment_db) == 0
    payment_boundaries["checkout"].assert_not_called()
    payment_boundaries["decision_hook"].assert_not_called()

    _add_decision(payment_db, domain.id)
    executed = client.post(
        "/api/payments/execute",
        json={"domain_id": domain.id},
    )
    assert executed.status_code == 200
    assert _attempt_count(payment_db) == 1


def test_reconciliation_rejects_uncovered_and_extra_browser_fields(
    client: TestClient,
    payment_db: Session,
    payment_boundaries: dict[str, Mock],
) -> None:
    """Mandate sync fails closed and never accepts raw browser mandate facts."""
    domain = _add_domain(payment_db)
    payment_boundaries["provider"].return_value = [_provider(currency="EUR")]

    uncovered = client.post(
        "/api/payments/mandate/reconcile",
        json={"domain_id": domain.id},
    )
    extra = client.post(
        "/api/payments/mandate/reconcile",
        json={"domain_id": domain.id, "mandate_id": "mdt_browser_controlled"},
    )

    assert uncovered.status_code == 409
    assert uncovered.json() == {
        "detail": "No approved mandate covers the current renewal"
    }
    assert extra.status_code == 422
    assert payment_db.scalar(select(func.count()).select_from(Mandate)) == 0
    assert _attempt_count(payment_db) == 0
    payment_boundaries["checkout"].assert_not_called()


def test_execute_missing_domain_returns_404_without_provider_calls(
    client: TestClient,
    payment_db: Session,
    payment_boundaries: dict[str, Mock],
) -> None:
    """An unknown domain is uncovered before any provider boundary."""
    response = client.post("/api/payments/execute", json={"domain_id": 999})

    assert response.status_code == 404
    assert response.json() == {"detail": "Domain not found"}
    payment_boundaries["provider"].assert_not_called()
    payment_boundaries["checkout"].assert_not_called()
    assert _attempt_count(payment_db) == 0


@pytest.mark.parametrize("decision", [None, "flag_for_review", "ignore"])
def test_execute_requires_latest_final_auto_renew_without_provider_calls(
    decision: str | None,
    client: TestClient,
    payment_db: Session,
    payment_boundaries: dict[str, Mock],
) -> None:
    """Missing or non-auto final decisions cannot reach Prava."""
    domain = _add_domain(payment_db)
    if decision is not None:
        _add_decision(payment_db, domain.id, decision=decision)

    response = client.post("/api/payments/execute", json={"domain_id": domain.id})

    assert response.status_code == 409
    payment_boundaries["provider"].assert_not_called()
    payment_boundaries["checkout"].assert_not_called()
    assert _attempt_count(payment_db) == 0


@pytest.mark.parametrize(
    "mandate_changes",
    [
        None,
        {"status": "inactive"},
        {"valid_until": NOW - timedelta(seconds=1)},
        {"merchant_name": "Other Registrar"},
        {"merchant_url": "https://other.example/renew"},
        {"merchant_country": "GB"},
        {"currency": "EUR"},
        {"cap_amount": Decimal("17.99")},
    ],
    ids=[
        "missing",
        "inactive",
        "expired",
        "merchant-name",
        "merchant-url",
        "merchant-country",
        "currency",
        "cap",
    ],
)
def test_uncovered_persisted_facts_never_reach_the_provider(
    mandate_changes: dict[str, object] | None,
    client: TestClient,
    payment_db: Session,
    payment_boundaries: dict[str, Mock],
) -> None:
    """Every locally uncovered request fails before any provider boundary."""
    domain = _add_domain(payment_db)
    _add_decision(payment_db, domain.id)
    if mandate_changes is not None:
        _add_mandate(payment_db, domain.id, **mandate_changes)

    response = client.post("/api/payments/execute", json={"domain_id": domain.id})

    assert response.status_code == 409
    assert response.json() == {
        "detail": "Renewal is not covered by an active mandate"
    }
    payment_boundaries["provider"].assert_not_called()
    payment_boundaries["checkout"].assert_not_called()
    payment_boundaries["decision_hook"].assert_not_called()
    assert _attempt_count(payment_db) == 0


@pytest.mark.parametrize(
    "provider_changes",
    [{"status": "inactive"}, {"frequency": "monthly"}],
    ids=["inactive", "non-yearly"],
)
def test_changed_provider_facts_fail_after_lookup_without_a_payment_attempt(
    provider_changes: dict[str, object],
    client: TestClient,
    payment_db: Session,
    payment_boundaries: dict[str, Mock],
) -> None:
    """A fresh provider mismatch cannot use previously valid persisted facts."""
    domain = _add_domain(payment_db)
    _add_decision(payment_db, domain.id)
    _add_mandate(payment_db, domain.id)
    payment_boundaries["provider"].return_value = [_provider(**provider_changes)]

    response = client.post("/api/payments/execute", json={"domain_id": domain.id})

    assert response.status_code == 409
    payment_boundaries["provider"].assert_called_once()
    payment_boundaries["checkout"].assert_not_called()
    assert _attempt_count(payment_db) == 0


def test_stale_quote_fails_closed_before_charge(
    client: TestClient,
    payment_db: Session,
    payment_boundaries: dict[str, Mock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale fresh-price boundary cannot authorize a provider charge."""
    domain = _add_domain(payment_db)
    _add_decision(payment_db, domain.id)
    _add_mandate(payment_db, domain.id)

    def stale_quote(domain_id: int, _: datetime) -> RenewalQuote:
        return RenewalQuote(
            domain_id=domain_id,
            merchant_name=DEMO_MERCHANT_NAME,
            merchant_url=DEMO_MERCHANT_URL,
            merchant_country=DEMO_MERCHANT_COUNTRY,
            amount=DEMO_RENEWAL_AMOUNT,
            currency=DEMO_CURRENCY,
            observed_at=NOW - timedelta(minutes=6),
        )

    monkeypatch.setattr(payment_routes, "get_current_execution_quote", stale_quote)

    response = client.post("/api/payments/execute", json={"domain_id": domain.id})

    assert response.status_code == 409
    payment_boundaries["provider"].assert_not_called()
    payment_boundaries["checkout"].assert_not_called()
    assert _attempt_count(payment_db) == 0


def test_provider_lookup_failure_is_sanitized_and_creates_no_attempt(
    client: TestClient,
    payment_db: Session,
    payment_boundaries: dict[str, Mock],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Provider diagnostics never enter the lookup failure response."""
    domain = _add_domain(payment_db)
    _add_decision(payment_db, domain.id)
    _add_mandate(payment_db, domain.id)
    secret_text = "raw provider payload with mdt_secret"
    payment_boundaries["provider"].side_effect = PravaMandateError(secret_text)

    response = client.post("/api/payments/execute", json={"domain_id": domain.id})

    assert response.status_code == 502
    assert response.json() == {"detail": "Prava mandate lookup failed"}
    assert secret_text not in response.text
    assert secret_text not in caplog.text
    payment_boundaries["checkout"].assert_not_called()
    assert _attempt_count(payment_db) == 0


def test_charge_failure_is_sanitized_and_marks_authorized_attempt(
    client: TestClient,
    payment_db: Session,
    payment_boundaries: dict[str, Mock],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A covered charge failure is recorded without leaking provider text."""
    domain = _add_domain(payment_db)
    _add_decision(payment_db, domain.id)
    _add_mandate(payment_db, domain.id)
    secret_text = "provider token=4111111111111111 cvv=999"
    payment_boundaries["checkout"].side_effect = PravaMandateError(secret_text)

    response = client.post("/api/payments/execute", json={"domain_id": domain.id})

    assert response.status_code == 502
    assert response.json() == {"detail": "Prava mandate charge failed"}
    assert secret_text not in response.text
    assert secret_text not in caplog.text
    attempt = payment_db.scalars(select(PaymentAttempt)).one()
    assert attempt.status == "charge_failed"
    assert attempt.merchant_order_ref is None


def test_merchant_checkout_failure_is_not_completed(
    client: TestClient,
    payment_db: Session,
    payment_boundaries: dict[str, Mock],
) -> None:
    """Merchant rejection remains an honest non-completed result."""
    domain = _add_domain(payment_db)
    _add_decision(payment_db, domain.id)
    _add_mandate(payment_db, domain.id)
    payment_boundaries["checkout"].return_value = _outcome(
        completed=False,
        payment_status="declined",
        merchant_order_ref=None,
        prava_report_status="completed",
    )

    response = client.post("/api/payments/execute", json={"domain_id": domain.id})

    assert response.status_code == 200
    assert response.json() == {
        "payment_status": "declined",
        "merchant_order_ref": None,
        "completed": False,
    }
    attempt = payment_db.scalars(select(PaymentAttempt)).one()
    assert attempt.status == "declined"


def test_outcome_report_failure_preserves_merchant_completion_for_reconciliation(
    client: TestClient,
    payment_db: Session,
    payment_boundaries: dict[str, Mock],
) -> None:
    """A completed checkout with failed reporting is not called completed status."""
    domain = _add_domain(payment_db)
    _add_decision(payment_db, domain.id)
    _add_mandate(payment_db, domain.id)
    payment_boundaries["checkout"].return_value = _outcome(
        payment_status="reconciliation_required",
        prava_report_status=None,
    )

    response = client.post("/api/payments/execute", json={"domain_id": domain.id})

    assert response.status_code == 200
    assert response.json() == {
        "payment_status": "reconciliation_required",
        "merchant_order_ref": "DEMO-REN-20260802-ABC12345",
        "completed": True,
    }
    attempt = payment_db.scalars(select(PaymentAttempt)).one()
    assert attempt.status == "reconciliation_required"


def test_database_failure_response_never_leaks_raw_diagnostics(
    client: TestClient,
    payment_db: Session,
    payment_boundaries: dict[str, Mock],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Database failures retain the sanitized route contract."""
    domain = _add_domain(payment_db)
    _add_decision(payment_db, domain.id)
    secret_text = "database password and raw row diagnostics"

    def fail(*_args: object, **_kwargs: object) -> object:
        raise SQLAlchemyError(secret_text)

    monkeypatch.setattr(payment_routes, "_latest_prerequisites", fail)

    response = client.post("/api/payments/execute", json={"domain_id": domain.id})

    assert response.status_code == 503
    assert response.json() == {"detail": "Payment data is unavailable"}
    assert secret_text not in response.text
    assert secret_text not in caplog.text
    payment_boundaries["provider"].assert_not_called()


def test_execute_forbids_browser_controlled_payment_fields(
    client: TestClient,
    payment_db: Session,
    payment_boundaries: dict[str, Mock],
) -> None:
    """Extra mandate, amount, merchant, or credential input is rejected."""
    domain = _add_domain(payment_db)
    _add_decision(payment_db, domain.id)

    response = client.post(
        "/api/payments/execute",
        json={
            "domain_id": domain.id,
            "amount": "1.00",
            "mandate_id": "mdt_browser_controlled",
            "token": "not-allowed",
        },
    )

    assert response.status_code == 422
    payment_boundaries["provider"].assert_not_called()
    payment_boundaries["checkout"].assert_not_called()
    assert _attempt_count(payment_db) == 0


def test_reconciliation_reuses_stable_mandate_record_and_refreshes_metadata(
    client: TestClient,
    payment_db: Session,
) -> None:
    """A matching provider digest updates sanitized facts without duplication."""
    domain = _add_domain(payment_db)
    mandate = Mandate(
        domain_id=domain.id,
        provider_mandate_id_digest=digest_provider_mandate_id(RAW_PROVIDER_ID),
        merchant_name="Old name",
        merchant_url=DEMO_MERCHANT_URL,
        merchant_country=DEMO_MERCHANT_COUNTRY,
        cap_amount=Decimal("20.00"),
        currency=DEMO_CURRENCY,
        frequency="yearly",
        status="active",
        valid_until=NOW + timedelta(days=1),
    )
    payment_db.add(mandate)
    payment_db.commit()
    payment_db.refresh(mandate)
    original_id = mandate.id

    response = client.post(
        "/api/payments/mandate/reconcile",
        json={"domain_id": domain.id},
    )

    assert response.status_code == 200
    payment_db.expire_all()
    mandates = payment_db.scalars(select(Mandate)).all()
    assert len(mandates) == 1
    assert mandates[0].id == original_id
    assert mandates[0].merchant_name == DEMO_MERCHANT_NAME
    assert _attempt_count(payment_db) == 0
