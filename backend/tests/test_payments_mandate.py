"""Integration tests for POST /api/payments/mandate."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.db.connection import create_database_engine, create_session_factory
from backend.db import connection as db_connection
from backend.db.models import Base, Domain
from backend.main import create_app
from backend.payments.prava_mandate import MandateSessionResult, PravaMandateError


@pytest.fixture()
def mandate_db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Session]:
    """Provide an isolated SQLite DB and wire it into the process-wide factory."""
    database_url = f"sqlite:///{tmp_path / 'mandate.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    engine = create_database_engine(database_url)
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)

    monkeypatch.setattr(db_connection, "_engine", engine)
    monkeypatch.setattr(db_connection, "_session_factory", factory)

    session = factory()
    domain = Domain(domain="billing.aegis-demo.test")
    session.add(domain)
    session.commit()
    session.refresh(domain)
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
    return TestClient(create_app())


def _mandate_body(domain_id: int) -> dict[str, object]:
    return {
        "domain_id": domain_id,
        # DEMO: JOINT-2 selected self-owned demo registrar path.
        "merchant_name": "Aegis Demo Registrar",
        "merchant_url": "https://example.com",
        "merchant_country": "US",
        "cap_amount": 18.0,
        "currency": "USD",
        "frequency": "yearly",
    }


def test_create_mandate_returns_approval_url(
    client: TestClient,
    mandate_db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    domain = mandate_db.query(Domain).one()

    def fake_create(**kwargs: object) -> MandateSessionResult:
        assert kwargs["domain"] == "billing.aegis-demo.test"
        assert kwargs["merchant_name"] == "Aegis Demo Registrar"
        assert kwargs["cap_amount"] == 18.0
        return MandateSessionResult(
            session_id="ses_test",
            iframe_url="https://sandbox.collect.prava.space/?session=ses_test",
            expires_at="2026-08-01T12:00:00Z",
            order_id="ord_test",
        )

    monkeypatch.setattr(
        "backend.routes.payments.create_yearly_mandate_session",
        fake_create,
    )

    response = client.post("/api/payments/mandate", json=_mandate_body(domain.id))
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "pending_approval"
    assert payload["approval_url"].startswith("https://sandbox.collect.prava.space/")


def test_create_mandate_rejects_non_yearly_frequency(
    client: TestClient,
    mandate_db: Session,
) -> None:
    domain = mandate_db.query(Domain).one()
    body = _mandate_body(domain.id)
    body["frequency"] = "monthly"
    response = client.post("/api/payments/mandate", json=body)
    assert response.status_code == 400
    assert "yearly" in response.json()["detail"]


def test_create_mandate_missing_domain_returns_404(client: TestClient, mandate_db: Session) -> None:
    response = client.post("/api/payments/mandate", json=_mandate_body(9999))
    assert response.status_code == 404
    assert response.json()["detail"] == "Domain not found"


def test_create_mandate_prava_failure_is_mapped(
    client: TestClient,
    mandate_db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    domain = mandate_db.query(Domain).one()

    def fail(**_kwargs: object) -> MandateSessionResult:
        raise PravaMandateError("Invalid API key", status_code=401)

    monkeypatch.setattr(
        "backend.routes.payments.create_yearly_mandate_session",
        fail,
    )
    response = client.post("/api/payments/mandate", json=_mandate_body(domain.id))
    assert response.status_code == 502
    assert "Invalid API key" in response.json()["detail"]
