"""Offline integration tests for the locked detection API contracts."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.db.models import Domain
from backend.detection.cert_expiry import (
    CertExpiryResult,
    CertLookupError,
    CertLookupErrorKind,
)
from backend.detection.domain_expiry import (
    DomainExpiryResult,
    DomainLookupError,
    DomainLookupErrorKind,
)
from backend.detection.takeover_risk import (
    TakeoverLookupError,
    TakeoverLookupErrorKind,
    TakeoverRiskResult,
)
from backend.main import create_app
from backend.routes import detection as detection_routes

EXPIRY_DATE = date(2027, 8, 1)
CERT_EXPIRY = datetime(2027, 6, 1, 12, 30, tzinfo=UTC)
SCAN_FIELDS = {
    "id",
    "domain",
    "expiry_date",
    "cert_expiry_date",
    "dns_risk",
    "dns_risk_detail",
}
DOMAIN_FIELDS = {
    "id",
    "domain",
    "expiry_date",
    "cert_expiry_date",
    "dns_risk",
    "last_scanned",
}


@dataclass(frozen=True, slots=True)
class _DetectorMocks:
    """Mocks for every external detector invoked by the scan route."""

    rdap: Mock
    certificate: Mock
    takeover: Mock


@pytest.fixture
def detector_mocks(monkeypatch: pytest.MonkeyPatch) -> _DetectorMocks:
    """Install successful offline detector results by default."""
    rdap = Mock(
        side_effect=lambda domain: DomainExpiryResult(
            domain, EXPIRY_DATE, "Example Registrar", ("active",)
        )
    )
    certificate = Mock(
        side_effect=lambda domain: CertExpiryResult(
            domain, CERT_EXPIRY, "Example Issuer", "crt.sh"
        )
    )
    takeover = Mock(
        side_effect=lambda domain: TakeoverRiskResult(
            domain,
            True,
            "missing.s3.amazonaws.com",
            "AWS/S3",
            "high",
        )
    )
    monkeypatch.setattr(detection_routes, "get_domain_expiry", rdap)
    monkeypatch.setattr(detection_routes, "get_cert_expiry", certificate)
    monkeypatch.setattr(detection_routes, "check_takeover_risk", takeover)
    return _DetectorMocks(rdap, certificate, takeover)


@pytest.fixture
def client(db_session: Session) -> Iterator[TestClient]:
    """Create an API client whose session dependency uses isolated SQLite."""
    app = create_app()

    def override_session() -> Iterator[Session]:
        yield db_session

    app.dependency_overrides[detection_routes.get_db_session] = override_session
    with TestClient(app) as test_client:
        yield test_client


def _domain_count(session: Session) -> int:
    """Return the number of persisted domain rows."""
    return session.scalar(select(func.count()).select_from(Domain)) or 0


def test_list_domains_returns_empty_contract(
    client: TestClient, detector_mocks: _DetectorMocks
) -> None:
    """GET /api/domains returns an empty list without invoking detectors."""
    response = client.get("/api/domains")

    assert response.status_code == 200
    assert response.json() == []
    detector_mocks.rdap.assert_not_called()
    detector_mocks.certificate.assert_not_called()
    detector_mocks.takeover.assert_not_called()


def test_successful_scan_returns_exact_contract_and_persists(
    client: TestClient,
    db_session: Session,
    detector_mocks: _DetectorMocks,
) -> None:
    """All three successful detectors produce and persist the locked shape."""
    response = client.post("/api/scan", json={"domain": "example.com"})

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == SCAN_FIELDS
    assert payload["domain"] == "example.com"
    assert payload["expiry_date"] == EXPIRY_DATE.isoformat()
    assert datetime.fromisoformat(payload["cert_expiry_date"]).tzinfo is not None
    assert payload["dns_risk"] is True
    assert "confidence=high" in payload["dns_risk_detail"]
    assert "compromise" not in payload["dns_risk_detail"].lower()
    assert _domain_count(db_session) == 1


def test_list_domains_returns_only_stored_contract_fields(
    client: TestClient, detector_mocks: _DetectorMocks
) -> None:
    """GET /api/domains returns persisted scans without ORM relationships."""
    scan = client.post("/api/scan", json={"domain": "example.com"})

    response = client.get("/api/domains")

    assert scan.status_code == response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert set(payload[0]) == DOMAIN_FIELDS
    assert payload[0]["id"] == scan.json()["id"]
    assert payload[0]["domain"] == "example.com"
    assert datetime.fromisoformat(payload[0]["last_scanned"]).tzinfo is not None


def test_rescan_normalizes_and_updates_existing_row(
    client: TestClient,
    db_session: Session,
    detector_mocks: _DetectorMocks,
) -> None:
    """Equivalent domain input updates facts while retaining one stable ID."""
    first = client.post("/api/scan", json={"domain": "example.com"})
    detector_mocks.rdap.side_effect = lambda domain: DomainExpiryResult(
        domain, date(2028, 1, 2), None, ()
    )
    detector_mocks.takeover.side_effect = lambda domain: TakeoverRiskResult(
        domain, False, None, None, "none"
    )

    second = client.post("/api/scan", json={"domain": "EXAMPLE.COM."})

    assert first.status_code == second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["domain"] == "example.com"
    assert second.json()["expiry_date"] == "2028-01-02"
    assert second.json()["dns_risk"] is False
    assert _domain_count(db_session) == 1
    assert detector_mocks.rdap.call_args.args == ("example.com",)
    assert detector_mocks.certificate.call_args.args == ("example.com",)
    assert detector_mocks.takeover.call_args.args == ("example.com",)


def test_invalid_domain_returns_422_without_side_effects(
    client: TestClient,
    db_session: Session,
    detector_mocks: _DetectorMocks,
) -> None:
    """Invalid input calls no detector and creates no database row."""
    response = client.post(
        "/api/scan", json={"domain": "https://example.com/path"}
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid domain"}
    detector_mocks.rdap.assert_not_called()
    detector_mocks.certificate.assert_not_called()
    detector_mocks.takeover.assert_not_called()
    assert _domain_count(db_session) == 0


def test_rdap_failure_preserves_certificate_and_dns_results(
    client: TestClient, detector_mocks: _DetectorMocks
) -> None:
    """A classified RDAP failure yields null expiry and other successes."""
    detector_mocks.rdap.side_effect = DomainLookupError(
        DomainLookupErrorKind.PROVIDER_UNAVAILABLE,
        "example.com",
        "raw provider diagnostic",
    )

    response = client.post("/api/scan", json={"domain": "example.com"})

    assert response.status_code == 200
    assert response.json()["expiry_date"] is None
    assert response.json()["cert_expiry_date"] is not None
    assert response.json()["dns_risk"] is True
    detector_mocks.certificate.assert_called_once()
    detector_mocks.takeover.assert_called_once()


def test_certificate_failure_preserves_rdap_and_dns_results(
    client: TestClient, detector_mocks: _DetectorMocks
) -> None:
    """A classified certificate failure yields null TLS expiry only."""
    detector_mocks.certificate.side_effect = CertLookupError(
        CertLookupErrorKind.TIMEOUT,
        "example.com",
        "raw TLS diagnostic",
    )

    response = client.post("/api/scan", json={"domain": "example.com"})

    assert response.status_code == 200
    assert response.json()["expiry_date"] == EXPIRY_DATE.isoformat()
    assert response.json()["cert_expiry_date"] is None
    assert response.json()["dns_risk"] is True
    detector_mocks.rdap.assert_called_once()
    detector_mocks.takeover.assert_called_once()


def test_dns_failure_preserves_other_results_and_sanitizes_detail(
    client: TestClient, detector_mocks: _DetectorMocks
) -> None:
    """A classified DNS failure stays false and exposes only its category."""
    detector_mocks.takeover.side_effect = TakeoverLookupError(
        TakeoverLookupErrorKind.DNS_TIMEOUT,
        "example.com",
        "secret raw DNS diagnostic",
    )

    response = client.post("/api/scan", json={"domain": "example.com"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["expiry_date"] == EXPIRY_DATE.isoformat()
    assert payload["cert_expiry_date"] is not None
    assert payload["dns_risk"] is False
    assert "category=dns_timeout" in payload["dns_risk_detail"]
    assert "secret" not in payload["dns_risk_detail"]
    detector_mocks.rdap.assert_called_once()
    detector_mocks.certificate.assert_called_once()


def test_pattern_only_is_uncertain_and_not_dns_risk(
    client: TestClient, detector_mocks: _DetectorMocks
) -> None:
    """An unconfirmed provider pattern never sets the persisted risk flag."""
    detector_mocks.takeover.side_effect = lambda domain: TakeoverRiskResult(
        domain,
        True,
        "missing.s3.amazonaws.com",
        "AWS/S3",
        "pattern_only",
    )

    response = client.post("/api/scan", json={"domain": "example.com"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["dns_risk"] is False
    assert "confidence=pattern_only" in payload["dns_risk_detail"]
    assert "uncertain" in payload["dns_risk_detail"]


def test_database_failure_rolls_back_and_returns_safe_error(
    client: TestClient,
    db_session: Session,
    detector_mocks: _DetectorMocks,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A persistence failure rolls back without returning database details."""
    rollback = Mock(wraps=db_session.rollback)
    monkeypatch.setattr(db_session, "rollback", rollback)
    monkeypatch.setattr(
        db_session,
        "flush",
        Mock(side_effect=SQLAlchemyError("secret database diagnostics")),
    )

    response = client.post("/api/scan", json={"domain": "example.com"})

    assert response.status_code == 500
    assert response.json() == {
        "detail": "Detection results could not be stored"
    }
    assert "secret" not in response.text
    rollback.assert_called_once()
    assert not db_session.new
    assert _domain_count(db_session) == 0
