"""Offline tests for strict, side-effect-free domain ranking."""

from __future__ import annotations

import ast
import json
import logging
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from openai import APITimeoutError, AuthenticationError, RateLimitError
from pydantic import ValidationError
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from backend.agent import ranking
from backend.agent.ranking import (
    DecisionResult,
    RankingError,
    RankingErrorKind,
    RankingResponse,
    rank_domains,
)
from backend.db.connection import (
    DatabaseConfigurationError,
    create_session_factory,
)
from backend.db.models import AgentDecision, Domain


class _FakeResponses:
    """Return queued parsed responses or raise queued SDK exceptions."""

    def __init__(self, outcomes: list[object]) -> None:
        self._outcomes = list(outcomes)
        self.calls: list[dict[str, object]] = []

    def parse(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(kwargs)
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return SimpleNamespace(output_parsed=outcome)


class _FakeClient:
    """Minimal OpenAI client surface used by the ranking module."""

    def __init__(self, outcomes: list[object]) -> None:
        self.responses = _FakeResponses(outcomes)


@pytest.fixture(autouse=True)
def isolate_ranking(monkeypatch: pytest.MonkeyPatch, db_engine: Engine) -> Mock:
    """Use the isolated database and block accidental live provider calls."""
    session_factory = create_session_factory(db_engine)
    monkeypatch.setattr(ranking, "get_session_factory", lambda: session_factory)

    def reject_live_client(**_: object) -> None:
        raise AssertionError("A ranking test attempted to create a live client")

    monkeypatch.setattr(ranking, "OpenAI", reject_live_client)
    sleep = Mock()
    monkeypatch.setattr(ranking, "_sleep", sleep)
    return sleep


def _install_client(
    monkeypatch: pytest.MonkeyPatch, outcomes: list[object]
) -> _FakeClient:
    """Install and return a deterministic fake provider client."""
    client = _FakeClient(outcomes)
    monkeypatch.setattr(ranking, "_create_openai_client", lambda: client)
    return client


def _add_domain(
    session: Session,
    *,
    name: str = "rank.example.com",
    expiry_days: int | None = 120,
    cert_days: int | None = 90,
    dns_risk: bool = False,
    dns_detail: str | None = None,
) -> Domain:
    """Persist one domain with expiry offsets relative to the test date."""
    today = date.today()
    now = datetime.now(UTC)
    domain = Domain(
        domain=name,
        expiry_date=today + timedelta(days=expiry_days)
        if expiry_days is not None
        else None,
        cert_expiry_date=now + timedelta(days=cert_days)
        if cert_days is not None
        else None,
        dns_risk=dns_risk,
        dns_risk_detail=dns_detail,
        last_scanned=now,
    )
    session.add(domain)
    session.commit()
    session.refresh(domain)
    return domain


def _decision(
    domain_id: int,
    *,
    score: int = 50,
    decision: str = "flag_for_review",
    reason: str = "Review the observed infrastructure signals.",
) -> DecisionResult:
    """Build one valid strict result for a fake provider response."""
    return DecisionResult(
        domain_id=domain_id,
        criticality_score=score,
        decision=decision,  # type: ignore[arg-type]
        reason=reason,
    )


def _response(*results: DecisionResult) -> RankingResponse:
    """Build a valid parsed batch response."""
    return RankingResponse(results=list(results))


def _prompt_domains(client: _FakeClient) -> list[dict[str, object]]:
    """Decode the deterministic input payload captured by the fake client."""
    payload = client.responses.calls[-1]["input"]
    assert isinstance(payload, str)
    parsed = json.loads(payload)
    assert isinstance(parsed, dict)
    return parsed["domains"]


@pytest.mark.parametrize(
    (
        "expiry_days",
        "cert_days",
        "dns_risk",
        "score",
        "decision",
        "fact_name",
        "fact_value",
    ),
    [
        (3, 180, False, 96, "auto_renew", "days_to_expiry", 3),
        (180, 180, True, 99, "flag_for_review", "dns_risk", True),
        (180, 4, False, 78, "flag_for_review", "days_to_cert_expiry", 4),
        (365, 180, False, 8, "ignore", "days_to_expiry", 365),
        (None, None, False, 50, "flag_for_review", "expiry_date", None),
    ],
)
def test_ranking_supplies_each_required_signal(
    monkeypatch: pytest.MonkeyPatch,
    db_session: Session,
    expiry_days: int | None,
    cert_days: int | None,
    dns_risk: bool,
    score: int,
    decision: str,
    fact_name: str,
    fact_value: object,
) -> None:
    """Supply urgent, DNS, TLS, healthy, and incomplete facts to the model."""
    domain = _add_domain(
        db_session,
        expiry_days=expiry_days,
        cert_days=cert_days,
        dns_risk=dns_risk,
        dns_detail="Confirmed provider fingerprint" if dns_risk else None,
    )
    result = _decision(
        domain.id,
        score=score,
        decision=decision,
        reason="The bounded observed facts support this recommendation.",
    )
    client = _install_client(monkeypatch, [_response(result)])

    assert rank_domains([domain.id]) == [result]
    assert _prompt_domains(client)[0][fact_name] == fact_value


def test_multiple_domains_are_ranked_once_and_returned_in_input_order(
    monkeypatch: pytest.MonkeyPatch, db_session: Session
) -> None:
    """Use one batched request and preserve input order, including duplicates."""
    first = _add_domain(db_session, name="first.example.com")
    second = _add_domain(db_session, name="second.example.com")
    first_result = _decision(first.id, score=20, decision="ignore")
    second_result = _decision(second.id, score=80)
    client = _install_client(
        monkeypatch,
        [_response(second_result, first_result)],
    )

    results = rank_domains([second.id, first.id, second.id])

    assert [item.domain_id for item in results] == [second.id, first.id, second.id]
    assert len(client.responses.calls) == 1
    assert [item["domain_id"] for item in _prompt_domains(client)] == [
        second.id,
        first.id,
    ]


def test_empty_input_returns_without_database_or_provider_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return an empty result without crossing an external boundary."""
    database = Mock(side_effect=AssertionError("database should not be read"))
    provider = Mock(side_effect=AssertionError("provider should not be called"))
    monkeypatch.setattr(ranking, "get_session_factory", database)
    monkeypatch.setattr(ranking, "_create_openai_client", provider)

    assert rank_domains([]) == []
    database.assert_not_called()
    provider.assert_not_called()


def test_batch_over_cost_limit_returns_fallback_without_external_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Apply the conservative fallback when the unique-ID cost cap is exceeded."""
    database = Mock(side_effect=AssertionError("database should not be read"))
    provider = Mock(side_effect=AssertionError("provider should not be called"))
    monkeypatch.setattr(ranking, "get_session_factory", database)
    monkeypatch.setattr(ranking, "_create_openai_client", provider)
    domain_ids = list(range(1, ranking.MAX_DOMAINS + 2))

    results = rank_domains(domain_ids)

    assert [result.domain_id for result in results] == domain_ids
    assert {result.decision for result in results} == {"flag_for_review"}
    database.assert_not_called()
    provider.assert_not_called()


@pytest.mark.parametrize("domain_ids", [[0], [-1], [True], ["1"], (1,)])
def test_invalid_input_is_rejected(domain_ids: object) -> None:
    """Reject non-list or non-positive integer identifiers."""
    with pytest.raises(RankingError) as caught:
        rank_domains(domain_ids)  # type: ignore[arg-type]
    assert caught.value.kind is RankingErrorKind.INVALID_INPUT


@pytest.mark.parametrize(
    "payload",
    [
        {
            "results": [
                {
                    "domain_id": 1,
                    "criticality_score": "90",
                    "decision": "auto_renew",
                    "reason": "Urgent.",
                }
            ]
        },
        {
            "results": [
                {
                    "domain_id": 1,
                    "criticality_score": 90,
                    "decision": "renew_now",
                    "reason": "Urgent.",
                }
            ]
        },
        {
            "results": [
                {
                    "domain_id": 1,
                    "criticality_score": 90,
                    "decision": "auto_renew",
                    "reason": "Urgent.",
                    "extra": True,
                }
            ]
        },
        {
            "results": [
                {
                    "domain_id": 1,
                    "criticality_score": 101,
                    "decision": "auto_renew",
                    "reason": "Urgent.",
                }
            ]
        },
    ],
)
def test_schema_violations_fall_back_to_manual_review(
    monkeypatch: pytest.MonkeyPatch, db_session: Session, payload: object
) -> None:
    """Convert malformed structured output into a safe standardized result."""
    domain = _add_domain(db_session)
    client = _install_client(monkeypatch, [payload])

    result = rank_domains([domain.id])[0]

    assert result.domain_id == domain.id
    assert result.criticality_score == ranking.FALLBACK_SCORE
    assert result.decision == "flag_for_review"
    assert len(client.responses.calls) == 1


def test_decision_contract_is_strict_and_bounded() -> None:
    """Reject extra fields, invalid scores, invalid enums, and empty reasons."""
    with pytest.raises(ValidationError):
        DecisionResult.model_validate(
            {
                "domain_id": 1,
                "criticality_score": 10,
                "decision": "ignore",
                "reason": "Healthy.",
                "unexpected": "field",
            }
        )
    with pytest.raises(ValidationError):
        _decision(1, score=-1)
    with pytest.raises(ValidationError):
        _decision(1, decision="invalid")
    with pytest.raises(ValidationError):
        _decision(1, reason="\n\t")


@pytest.mark.parametrize("shape", ["duplicate", "missing", "extra"])
def test_result_id_mismatches_fall_back_for_every_loaded_domain(
    monkeypatch: pytest.MonkeyPatch,
    db_session: Session,
    shape: str,
) -> None:
    """Reject duplicate, missing, or unexpected result IDs as one bad batch."""
    first = _add_domain(db_session, name="one.example.com")
    second = _add_domain(db_session, name="two.example.com")
    if shape == "duplicate":
        parsed = _response(_decision(first.id), _decision(first.id))
    elif shape == "missing":
        parsed = _response(_decision(first.id))
    else:
        parsed = _response(
            _decision(first.id),
            _decision(second.id),
            _decision(999_999),
        )
    _install_client(monkeypatch, [parsed])

    results = rank_domains([first.id, second.id])

    assert [item.domain_id for item in results] == [first.id, second.id]
    assert {item.decision for item in results} == {"flag_for_review"}
    assert {item.criticality_score for item in results} == {ranking.FALLBACK_SCORE}


def test_refusal_or_missing_parsed_output_falls_back(
    monkeypatch: pytest.MonkeyPatch, db_session: Session
) -> None:
    """Treat absent parsed output as unusable without parsing response prose."""
    domain = _add_domain(db_session)
    _install_client(monkeypatch, [None])

    result = rank_domains([domain.id])[0]

    assert result.decision == "flag_for_review"
    assert result.criticality_score == ranking.FALLBACK_SCORE


def test_timeout_is_retried_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
    db_session: Session,
    isolate_ranking: Mock,
) -> None:
    """Retry a transient timeout and return the later structured success."""
    domain = _add_domain(db_session)
    timeout = APITimeoutError(request=httpx.Request("POST", "https://api.openai.com"))
    expected = _decision(domain.id, score=88, decision="auto_renew")
    client = _install_client(monkeypatch, [timeout, _response(expected)])

    assert rank_domains([domain.id]) == [expected]
    assert len(client.responses.calls) == 2
    assert isolate_ranking.call_count == 1


def test_rate_limit_is_retried_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
    db_session: Session,
    isolate_ranking: Mock,
) -> None:
    """Retry a rate limit once through the application retry boundary."""
    domain = _add_domain(db_session)
    request = httpx.Request("POST", "https://api.openai.com")
    limited = RateLimitError(
        "rate limited",
        response=httpx.Response(429, request=request),
        body=None,
    )
    expected = _decision(domain.id)
    client = _install_client(monkeypatch, [limited, _response(expected)])

    assert rank_domains([domain.id]) == [expected]
    assert len(client.responses.calls) == 2
    assert isolate_ranking.call_count == 1


def test_transient_exhaustion_uses_fallback_without_raw_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
    db_session: Session,
    isolate_ranking: Mock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Stop after three total attempts and keep provider diagnostics private."""
    caplog.set_level(logging.WARNING)
    domain = _add_domain(db_session)
    errors = [
        APITimeoutError(request=httpx.Request("POST", "https://api.openai.com"))
        for _ in range(3)
    ]
    client = _install_client(monkeypatch, errors)

    result = rank_domains([domain.id])[0]

    assert result.decision == "flag_for_review"
    assert len(client.responses.calls) == ranking.MAX_ATTEMPTS
    assert isolate_ranking.call_count == ranking.MAX_ATTEMPTS - 1
    assert "api.openai.com" not in result.reason
    assert "api.openai.com" not in caplog.text


def test_permanent_provider_error_is_not_retried(
    monkeypatch: pytest.MonkeyPatch,
    db_session: Session,
    isolate_ranking: Mock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Avoid retries for permanent authentication failures."""
    caplog.set_level(logging.WARNING)
    domain = _add_domain(db_session)
    request = httpx.Request("POST", "https://api.openai.com")
    denied = AuthenticationError(
        "secret provider response",
        response=httpx.Response(401, request=request),
        body=None,
    )
    client = _install_client(monkeypatch, [denied])

    result = rank_domains([domain.id])[0]

    assert result.decision == "flag_for_review"
    assert len(client.responses.calls) == 1
    isolate_ranking.assert_not_called()
    assert "secret provider response" not in caplog.text
    assert "secret provider response" not in result.reason


def test_missing_database_id_gets_fallback_without_entering_model_batch(
    monkeypatch: pytest.MonkeyPatch, db_session: Session
) -> None:
    """Rank found rows and return a safe result for an absent requested ID."""
    domain = _add_domain(db_session)
    expected = _decision(domain.id, score=12, decision="ignore")
    client = _install_client(monkeypatch, [_response(expected)])

    results = rank_domains([999_999, domain.id])

    assert [item.domain_id for item in results] == [999_999, domain.id]
    assert results[0].decision == "flag_for_review"
    assert results[1] == expected
    assert [item["domain_id"] for item in _prompt_domains(client)] == [domain.id]


def test_database_failure_returns_fallback_without_exception_text(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Sanitize database failures and return one result per requested ID."""
    caplog.set_level(logging.WARNING)

    def fail_database() -> None:
        raise DatabaseConfigurationError("postgres://user:raw-secret@host/db")

    monkeypatch.setattr(ranking, "get_session_factory", fail_database)

    results = rank_domains([2, 1, 2])

    assert [item.domain_id for item in results] == [2, 1, 2]
    assert {item.decision for item in results} == {"flag_for_review"}
    combined = caplog.text + " ".join(item.reason for item in results)
    assert "raw-secret" not in combined


def test_only_allowlisted_facts_and_two_recent_decisions_are_sent(
    monkeypatch: pytest.MonkeyPatch, db_session: Session
) -> None:
    """Bound history and omit all non-ranking persistence fields from input."""
    domain = _add_domain(
        db_session,
        dns_risk=True,
        dns_detail="x" * (ranking.MAX_SOURCE_DETAIL + 40),
    )
    for index in range(3):
        db_session.add(
            AgentDecision(
                domain_id=domain.id,
                criticality_score=40 + index,
                decision="flag_for_review",
                reason=f"history {index} " + "y" * 200,
                created_at=datetime.now(UTC) + timedelta(seconds=index),
            )
        )
    db_session.commit()
    expected = _decision(domain.id)
    client = _install_client(monkeypatch, [_response(expected)])

    rank_domains([domain.id])

    fact = _prompt_domains(client)[0]
    assert set(fact) == {
        "domain_id",
        "domain",
        "expiry_date",
        "days_to_expiry",
        "cert_expiry_date",
        "days_to_cert_expiry",
        "dns_risk",
        "dns_risk_detail",
        "recent_decisions",
    }
    assert len(fact["dns_risk_detail"]) == ranking.MAX_SOURCE_DETAIL
    assert len(fact["recent_decisions"]) == ranking.MAX_HISTORY_PER_DOMAIN
    assert all(
        len(item["reason"]) <= ranking.MAX_HISTORY_REASON
        for item in fact["recent_decisions"]
    )


def test_ranking_does_not_mutate_database_or_import_side_effect_integrations(
    monkeypatch: pytest.MonkeyPatch, db_session: Session
) -> None:
    """Keep ranking read-only and disconnected from execution integrations."""
    domain = _add_domain(db_session)
    history = AgentDecision(
        domain_id=domain.id,
        criticality_score=25,
        decision="ignore",
        reason="Previously healthy.",
    )
    db_session.add(history)
    db_session.commit()
    before_domain = db_session.execute(
        select(
            Domain.expiry_date,
            Domain.cert_expiry_date,
            Domain.dns_risk,
            Domain.dns_risk_detail,
            Domain.last_scanned,
        ).where(Domain.id == domain.id)
    ).one()
    before_count = db_session.scalar(select(func.count(AgentDecision.id)))
    _install_client(monkeypatch, [_response(_decision(domain.id))])

    rank_domains([domain.id])
    db_session.expire_all()

    after_domain = db_session.execute(
        select(
            Domain.expiry_date,
            Domain.cert_expiry_date,
            Domain.dns_risk,
            Domain.dns_risk_detail,
            Domain.last_scanned,
        ).where(Domain.id == domain.id)
    ).one()
    after_count = db_session.scalar(select(func.count(AgentDecision.id)))
    assert after_domain == before_domain
    assert after_count == before_count

    source = Path(ranking.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules = {
        node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    }
    imported_names = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    called_names = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    called_names.update(
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    )
    forbidden_integration_terms = (
        "on_agent_" + "decision",
        "pay" + "ment",
        "man" + "date",
        "check" + "out",
    )
    import_surface = {name.lower() for name in imported_modules | imported_names}
    call_surface = {name.lower() for name in called_names}
    assert not any(
        term in name
        for term in forbidden_integration_terms
        for name in import_surface | call_surface
    )


def test_request_uses_locked_model_schema_and_output_bounds(
    monkeypatch: pytest.MonkeyPatch, db_session: Session
) -> None:
    """Use the locked model with strict parsing and bounded output tokens."""
    domain = _add_domain(db_session)
    client = _install_client(monkeypatch, [_response(_decision(domain.id))])

    rank_domains([domain.id])

    call = client.responses.calls[0]
    assert call["model"] == "gpt-4o-mini"
    assert call["text_format"] is RankingResponse
    assert call["max_output_tokens"] == ranking.MAX_OUTPUT_TOKENS
    assert call["store"] is False


def test_sdk_client_disables_internal_retries_and_sets_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Configure one retry owner and a bounded request timeout."""
    constructor = Mock(return_value=object())
    monkeypatch.setattr(ranking, "OpenAI", constructor)

    assert ranking._create_openai_client() is constructor.return_value
    constructor.assert_called_once_with(
        max_retries=0,
        timeout=ranking.REQUEST_TIMEOUT_SECONDS,
    )
