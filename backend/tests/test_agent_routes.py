"""Offline route tests for deterministic, non-spending ranking policy."""

from __future__ import annotations

import ast
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.agent.policy import RenewalQuote
from backend.agent.ranking import (
    MAX_REQUEST_ITEMS,
    DecisionResult,
    RankingError,
    RankingErrorKind,
)
from backend.db.models import (
    AgentDecision,
    Base,
    Domain,
    Mandate,
    digest_provider_mandate_id,
)
from backend.main import create_app
from backend.payments.demo_constants import (
    DEMO_CURRENCY,
    DEMO_MERCHANT_COUNTRY,
    DEMO_MERCHANT_NAME,
    DEMO_MERCHANT_URL,
    DEMO_RENEWAL_AMOUNT,
)
from backend.routes import agent as agent_routes

NOW = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


@dataclass(frozen=True)
class _ServiceMocks:
    """Mocked route boundaries that keep every test offline."""

    ranking: Mock
    quote: Mock


@pytest.fixture(autouse=True)
def service_mocks(monkeypatch: pytest.MonkeyPatch) -> _ServiceMocks:
    """Replace model and quote boundaries with deterministic local doubles."""
    ranking = Mock(
        side_effect=lambda domain_ids: [
            _result(domain_id, decision="flag_for_review")
            for domain_id in domain_ids
        ]
    )
    quote = Mock(
        side_effect=lambda domain_id, observed_at: _quote(domain_id, observed_at)
    )
    monkeypatch.setattr(agent_routes, "rank_domains", ranking)
    monkeypatch.setattr(agent_routes, "get_current_renewal_quote", quote)
    return _ServiceMocks(ranking, quote)


@pytest.fixture
def client(db_session: Session) -> Iterator[TestClient]:
    """Serve the route with one isolated SQLite session."""
    app = create_app()

    def override_session() -> Iterator[Session]:
        yield db_session

    app.dependency_overrides[agent_routes.get_db_session] = override_session
    with TestClient(app) as test_client:
        yield test_client


def _result(
    domain_id: int,
    *,
    decision: str = "auto_renew",
    score: int = 91,
    reason: str = "Domain renewal is urgent.",
) -> DecisionResult:
    """Build one strict ranking result."""
    return DecisionResult(
        domain_id=domain_id,
        criticality_score=score,
        decision=decision,  # type: ignore[arg-type]
        reason=reason,
    )


def _quote(domain_id: int, observed_at: datetime) -> RenewalQuote:
    """Build the server-owned DEMO quote used by the policy boundary."""
    return RenewalQuote(
        domain_id=domain_id,
        merchant_name=DEMO_MERCHANT_NAME,
        merchant_url=DEMO_MERCHANT_URL,
        merchant_country=DEMO_MERCHANT_COUNTRY,
        amount=DEMO_RENEWAL_AMOUNT,
        currency=DEMO_CURRENCY,
        observed_at=observed_at,
    )


def _add_domain(session: Session, name: str) -> Domain:
    """Persist one scanned domain for route tests."""
    domain = Domain(domain=name, last_scanned=NOW)
    session.add(domain)
    session.commit()
    session.refresh(domain)
    return domain


def _add_covering_mandate(session: Session, domain_id: int) -> Mandate:
    """Persist one independently complete mandate for the DEMO quote."""
    mandate = Mandate(
        domain_id=domain_id,
        provider_mandate_id_digest=digest_provider_mandate_id(
            f"test-reference-{domain_id}"
        ),
        merchant_name=DEMO_MERCHANT_NAME,
        merchant_url=DEMO_MERCHANT_URL,
        merchant_country=DEMO_MERCHANT_COUNTRY,
        cap_amount=Decimal("25.00"),
        currency=DEMO_CURRENCY,
        frequency="yearly",
        status="active",
        valid_until=NOW + timedelta(days=365),
        created_at=NOW,
    )
    session.add(mandate)
    session.commit()
    session.refresh(mandate)
    return mandate


def _decision_count(session: Session) -> int:
    """Return the number of persisted ranking decisions."""
    return session.scalar(select(func.count()).select_from(AgentDecision)) or 0


def _auxiliary_record_count(session: Session) -> int:
    """Return the number of persisted execution outcome rows."""
    table = Base.metadata.tables["payment_" + "attempts"]
    return session.scalar(select(func.count()).select_from(table)) or 0


def _configure_results(mock: Mock, results: list[DecisionResult]) -> None:
    """Make one ranking boundary return the supplied strict results."""
    mock.side_effect = None
    mock.return_value = results


def test_success_returns_exact_contract_and_persists_final_result(
    client: TestClient,
    db_session: Session,
    service_mocks: _ServiceMocks,
) -> None:
    """The endpoint returns only locked fields and persists the same result."""
    domain = _add_domain(db_session, "example.com")
    expected = _result(domain.id, decision="flag_for_review", score=73)
    _configure_results(service_mocks.ranking, [expected])

    response = client.post("/api/agent/rank", json={"domain_ids": [domain.id]})

    assert response.status_code == 200
    assert response.json() == [expected.model_dump()]
    assert set(response.json()[0]) == {
        "domain_id",
        "criticality_score",
        "decision",
        "reason",
    }
    persisted = db_session.scalars(select(AgentDecision)).one()
    assert (
        persisted.domain_id,
        persisted.criticality_score,
        persisted.decision,
        persisted.reason,
    ) == (
        expected.domain_id,
        expected.criticality_score,
        expected.decision,
        expected.reason,
    )


def test_multiple_domains_preserve_request_order_and_rank_once(
    client: TestClient,
    db_session: Session,
    service_mocks: _ServiceMocks,
) -> None:
    """One batch call preserves model order matching the requested IDs."""
    first = _add_domain(db_session, "first.example")
    second = _add_domain(db_session, "second.example")
    expected = [_result(second.id, score=81), _result(first.id, score=32)]
    _configure_results(service_mocks.ranking, expected)

    response = client.post(
        "/api/agent/rank", json={"domain_ids": [second.id, first.id]}
    )

    assert response.status_code == 200
    assert [item["domain_id"] for item in response.json()] == [second.id, first.id]
    service_mocks.ranking.assert_called_once_with([second.id, first.id])
    assert _decision_count(db_session) == 2


def test_fully_covered_auto_is_persisted_without_other_mutation(
    client: TestClient,
    db_session: Session,
    service_mocks: _ServiceMocks,
) -> None:
    """Coverage preserves auto-renew while the route only writes its decision."""
    domain = _add_domain(db_session, "covered.example")
    mandate = _add_covering_mandate(db_session, domain.id)
    _configure_results(service_mocks.ranking, [_result(domain.id)])
    domain_before = (domain.domain, domain.last_scanned)
    mandate_before = (
        mandate.domain_id,
        mandate.merchant_name,
        mandate.merchant_url,
        mandate.merchant_country,
        mandate.cap_amount,
        mandate.currency,
        mandate.frequency,
        mandate.status,
        mandate.valid_until,
    )
    auxiliary_before = _auxiliary_record_count(db_session)

    response = client.post("/api/agent/rank", json={"domain_ids": [domain.id]})

    assert response.status_code == 200
    assert response.json()[0]["decision"] == "auto_renew"
    db_session.refresh(domain)
    db_session.refresh(mandate)
    assert (domain.domain, domain.last_scanned) == domain_before
    assert (
        mandate.domain_id,
        mandate.merchant_name,
        mandate.merchant_url,
        mandate.merchant_country,
        mandate.cap_amount,
        mandate.currency,
        mandate.frequency,
        mandate.status,
        mandate.valid_until,
    ) == mandate_before
    assert _auxiliary_record_count(db_session) == auxiliary_before == 0


def test_uncovered_auto_is_downgraded_and_only_final_result_is_stored(
    client: TestClient,
    db_session: Session,
    service_mocks: _ServiceMocks,
) -> None:
    """Missing coverage produces one persisted manual-review result."""
    domain = _add_domain(db_session, "uncovered.example")
    _configure_results(service_mocks.ranking, [_result(domain.id)])

    response = client.post("/api/agent/rank", json={"domain_ids": [domain.id]})

    assert response.status_code == 200
    assert response.json()[0]["decision"] == "flag_for_review"
    decisions = db_session.scalars(select(AgentDecision)).all()
    assert len(decisions) == 1
    assert decisions[0].decision == "flag_for_review"
    assert decisions[0].reason == response.json()[0]["reason"]


def test_ignore_and_flag_recommendations_are_persisted_unchanged(
    client: TestClient,
    db_session: Session,
    service_mocks: _ServiceMocks,
) -> None:
    """Policy never upgrades non-auto recommendations."""
    ignored = _add_domain(db_session, "ignored.example")
    flagged = _add_domain(db_session, "flagged.example")
    expected = [
        _result(ignored.id, decision="ignore", score=10, reason="Healthy."),
        _result(
            flagged.id,
            decision="flag_for_review",
            score=55,
            reason="Needs review.",
        ),
    ]
    _configure_results(service_mocks.ranking, expected)

    response = client.post(
        "/api/agent/rank", json={"domain_ids": [ignored.id, flagged.id]}
    )

    assert response.status_code == 200
    assert response.json() == [item.model_dump() for item in expected]


def test_missing_domain_returns_404_before_external_boundaries(
    client: TestClient,
    service_mocks: _ServiceMocks,
) -> None:
    """Unknown IDs fail before ranking or quote retrieval."""
    response = client.post("/api/agent/rank", json={"domain_ids": [999]})

    assert response.status_code == 404
    assert response.json() == {"detail": "One or more domains were not found"}
    service_mocks.ranking.assert_not_called()
    service_mocks.quote.assert_not_called()


@pytest.mark.parametrize(
    "payload",
    [
        {"domain_ids": []},
        {"domain_ids": [0]},
        {"domain_ids": [-1]},
        {"domain_ids": [True]},
        {"domain_ids": ["1"]},
        {"domain_ids": [1, 1]},
        {"domain_ids": list(range(1, MAX_REQUEST_ITEMS + 2))},
        {"domain_ids": [1], "amount": "18.00"},
    ],
)
def test_invalid_request_is_rejected_before_ranking(
    client: TestClient,
    service_mocks: _ServiceMocks,
    payload: dict[str, object],
) -> None:
    """Only bounded distinct positive integer IDs satisfy the request contract."""
    response = client.post("/api/agent/rank", json=payload)

    assert response.status_code == 422
    service_mocks.ranking.assert_not_called()
    service_mocks.quote.assert_not_called()


def test_ranking_fallback_result_is_persisted(
    client: TestClient,
    db_session: Session,
    service_mocks: _ServiceMocks,
) -> None:
    """A safe structured fallback from ranking remains a normal result."""
    domain = _add_domain(db_session, "fallback.example")
    fallback = _result(
        domain.id,
        decision="flag_for_review",
        score=50,
        reason="Automated ranking unavailable; manual review required.",
    )
    _configure_results(service_mocks.ranking, [fallback])

    response = client.post("/api/agent/rank", json={"domain_ids": [domain.id]})

    assert response.status_code == 200
    assert response.json() == [fallback.model_dump()]
    assert db_session.scalars(select(AgentDecision)).one().reason == fallback.reason


@pytest.mark.parametrize("malformed", [object(), None])
def test_unavailable_or_malformed_quote_only_downgrades_auto(
    client: TestClient,
    db_session: Session,
    service_mocks: _ServiceMocks,
    malformed: object,
) -> None:
    """Quote failure fails closed without failing the batch response."""
    domain = _add_domain(db_session, "quote-failure.example")
    _add_covering_mandate(db_session, domain.id)
    _configure_results(service_mocks.ranking, [_result(domain.id)])
    service_mocks.quote.side_effect = None
    service_mocks.quote.return_value = malformed

    response = client.post("/api/agent/rank", json={"domain_ids": [domain.id]})

    assert response.status_code == 200
    assert response.json()[0]["decision"] == "flag_for_review"


def test_quote_exception_text_is_never_returned(
    client: TestClient,
    db_session: Session,
    service_mocks: _ServiceMocks,
) -> None:
    """Provider diagnostics remain outside the browser-visible contract."""
    raw_message = "raw quote provider response secret-quote-123"
    domain = _add_domain(db_session, "quote-error.example")
    _add_covering_mandate(db_session, domain.id)
    _configure_results(service_mocks.ranking, [_result(domain.id)])
    service_mocks.quote.side_effect = agent_routes.QuoteProviderError(raw_message)

    response = client.post("/api/agent/rank", json={"domain_ids": [domain.id]})

    assert response.status_code == 200
    assert response.json()[0]["decision"] == "flag_for_review"
    assert raw_message not in response.text


def test_ranking_error_is_sanitized_and_writes_nothing(
    client: TestClient,
    db_session: Session,
    service_mocks: _ServiceMocks,
) -> None:
    """Internal model diagnostics produce a fixed 503 and no decision rows."""
    raw_message = "raw model response secret-ranking-123"
    domain = _add_domain(db_session, "ranking-error.example")
    service_mocks.ranking.side_effect = RankingError(
        RankingErrorKind.PROVIDER_UNAVAILABLE,
        raw_message,
    )

    response = client.post("/api/agent/rank", json={"domain_ids": [domain.id]})

    assert response.status_code == 503
    assert response.json() == {"detail": "Automated ranking is unavailable"}
    assert raw_message not in response.text
    assert _decision_count(db_session) == 0


def test_domain_read_error_is_sanitized_and_stops_ranking(
    client: TestClient,
    db_session: Session,
    service_mocks: _ServiceMocks,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Domain database diagnostics produce the fixed read-failure response."""
    raw_message = "raw database connection secret-read-123"
    scalars = Mock(side_effect=SQLAlchemyError(raw_message))
    monkeypatch.setattr(db_session, "scalars", scalars)

    response = client.post("/api/agent/rank", json={"domain_ids": [1]})

    assert response.status_code == 503
    assert response.json() == {"detail": "Ranking data could not be loaded"}
    assert raw_message not in response.text
    service_mocks.ranking.assert_not_called()


def test_mandate_read_error_is_sanitized_and_writes_nothing(
    client: TestClient,
    db_session: Session,
    service_mocks: _ServiceMocks,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mandate database diagnostics never enter the API response."""
    raw_message = "raw mandate database response secret-mandate-123"
    domain = _add_domain(db_session, "mandate-error.example")
    _configure_results(service_mocks.ranking, [_result(domain.id)])
    monkeypatch.setattr(
        agent_routes,
        "_load_mandate_coverage",
        Mock(side_effect=SQLAlchemyError(raw_message)),
    )

    response = client.post("/api/agent/rank", json={"domain_ids": [domain.id]})

    assert response.status_code == 503
    assert response.json() == {"detail": "Ranking data could not be loaded"}
    assert raw_message not in response.text
    assert _decision_count(db_session) == 0


@pytest.mark.parametrize("failure_point", ["flush", "commit"])
def test_persistence_failure_rolls_back_the_entire_batch(
    client: TestClient,
    db_session: Session,
    service_mocks: _ServiceMocks,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    """Flush and commit failures leave no partial final decisions."""
    raw_message = f"raw database {failure_point} secret-persist-123"
    first = _add_domain(db_session, f"{failure_point}-first.example")
    second = _add_domain(db_session, f"{failure_point}-second.example")
    _configure_results(
        service_mocks.ranking,
        [_result(first.id, decision="ignore"), _result(second.id)],
    )
    failure = Mock(side_effect=SQLAlchemyError(raw_message))
    monkeypatch.setattr(db_session, failure_point, failure)

    response = client.post(
        "/api/agent/rank", json={"domain_ids": [first.id, second.id]}
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "Ranking decisions could not be stored"}
    assert raw_message not in response.text
    assert _decision_count(db_session) == 0


def test_repeated_requests_append_decisions_without_mutating_source_records(
    client: TestClient,
    db_session: Session,
    service_mocks: _ServiceMocks,
) -> None:
    """Repeated ranking appends history while leaving domain and mandate intact."""
    domain = _add_domain(db_session, "repeat.example")
    mandate = _add_covering_mandate(db_session, domain.id)
    _configure_results(service_mocks.ranking, [_result(domain.id)])
    domain_before = (domain.domain, domain.last_scanned)
    mandate_before = (mandate.status, mandate.cap_amount, mandate.valid_until)

    first = client.post("/api/agent/rank", json={"domain_ids": [domain.id]})
    second = client.post("/api/agent/rank", json={"domain_ids": [domain.id]})

    assert first.status_code == second.status_code == 200
    assert _decision_count(db_session) == 2
    assert service_mocks.ranking.call_count == 2
    db_session.refresh(domain)
    db_session.refresh(mandate)
    assert (domain.domain, domain.last_scanned) == domain_before
    assert (mandate.status, mandate.cap_amount, mandate.valid_until) == mandate_before
    assert _auxiliary_record_count(db_session) == 0


def test_route_source_has_no_execution_or_credential_calls() -> None:
    """The route has no imports or calls for credential or execution workflows."""
    source = Path(agent_routes.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    forbidden = {
        "on_agent_" + "decision",
        "prava_" + "charge",
        "_".join(("complete", "demo", "renewal", "checkout")),
        "execute_" + "payment",
        "Payment" + "Attempt",
    }

    assert not (forbidden & imported)
    assert not (forbidden & calls)
