"""Offline tests for strict, side-effect-free domain ranking."""

from __future__ import annotations

import ast
import json
import logging
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Self
from unittest.mock import Mock

import httpx
import pytest
from openai import APITimeoutError, AuthenticationError, RateLimitError
from openai.lib._parsing._responses import type_to_response_format_param
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

    def __init__(self: Self, outcomes: list[object]) -> None:
        self._outcomes = list(outcomes)
        self.calls: list[dict[str, object]] = []

    def parse(self: Self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(kwargs)
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return SimpleNamespace(output_parsed=outcome)


class _FakeClient:
    """Minimal OpenAI client surface used by the ranking module."""

    def __init__(self: Self, outcomes: list[object]) -> None:
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
    today = datetime.now(UTC).date()
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


def _assert_model_result(
    result: DecisionResult,
    *,
    domain_id: int,
    score: int,
    decision: str,
) -> None:
    """Assert stable model-result fields without locking reason wording."""
    assert result.domain_id == domain_id
    assert result.criticality_score == score
    assert result.decision == decision
    assert isinstance(result.reason, str)
    assert 0 < len(result.reason) <= ranking.MAX_REASON_LENGTH


def _schema_keys(value: object) -> set[str]:
    """Collect every mapping key from a nested generated JSON schema."""
    keys: set[str] = set()
    if isinstance(value, dict):
        keys.update(value)
        for item in value.values():
            keys.update(_schema_keys(item))
    elif isinstance(value, list):
        for item in value:
            keys.update(_schema_keys(item))
    return keys


def _prompt_domains(client: _FakeClient) -> list[dict[str, object]]:
    """Decode the deterministic input payload captured by the fake client."""
    payload = client.responses.calls[-1]["input"]
    assert isinstance(payload, str)
    parsed = json.loads(payload)
    assert isinstance(parsed, dict)
    return parsed["domains"]


@pytest.mark.parametrize(
    ("value", "today", "expected"),
    [
        (
            datetime(
                2026,
                1,
                2,
                1,
                tzinfo=timezone(timedelta(hours=5, minutes=30)),
            ),
            date(2026, 1, 1),
            0,
        ),
        (date(2026, 1, 3), date(2026, 1, 1), 2),
        (datetime(2026, 1, 2, 23, 30), date(2026, 1, 1), 1),
        (None, date(2026, 1, 1), None),
    ],
)
def test_days_until_uses_utc_for_aware_datetimes(
    value: date | datetime | None,
    today: date,
    expected: int | None,
) -> None:
    """Use UTC for aware datetimes and calendar dates for naive values."""
    assert ranking._days_until(value, today) == expected


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

    results = rank_domains([domain.id])

    assert len(results) == 1
    _assert_model_result(
        results[0],
        domain_id=domain.id,
        score=score,
        decision=decision,
    )
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


def test_duplicate_heavy_input_over_item_limit_is_rejected_before_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject an oversized caller list before database or provider access."""
    database = Mock(side_effect=AssertionError("database should not be read"))
    provider = Mock(side_effect=AssertionError("provider should not be called"))
    monkeypatch.setattr(ranking, "get_session_factory", database)
    monkeypatch.setattr(ranking, "_create_openai_client", provider)

    with pytest.raises(RankingError) as caught:
        rank_domains([1] * (ranking.MAX_REQUEST_ITEMS + 1))

    assert caught.value.kind is RankingErrorKind.INVALID_INPUT
    database.assert_not_called()
    provider.assert_not_called()


def test_exact_request_item_boundary_accepts_ordered_duplicates(
    monkeypatch: pytest.MonkeyPatch, db_session: Session
) -> None:
    """Accept the total-item boundary while deduplicating the model batch."""
    domain = _add_domain(db_session)
    client = _install_client(monkeypatch, [_response(_decision(domain.id))])
    domain_ids = [domain.id] * ranking.MAX_REQUEST_ITEMS

    results = rank_domains(domain_ids)

    assert [result.domain_id for result in results] == domain_ids
    assert len(client.responses.calls) == 1
    assert len(_prompt_domains(client)) == 1
    _assert_model_result(
        results[-1],
        domain_id=domain.id,
        score=50,
        decision="flag_for_review",
    )


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
        {
            "results": [
                {
                    "domain_id": 0,
                    "criticality_score": 90,
                    "decision": "auto_renew",
                    "reason": "Urgent.",
                }
            ]
        },
        {
            "results": [
                {
                    "domain_id": 1,
                    "criticality_score": -1,
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
                    "decision": "auto_renew",
                    "reason": "\n\t",
                }
            ]
        },
        {
            "results": [
                {
                    "domain_id": 1,
                    "criticality_score": 90,
                    "decision": "auto_renew",
                    "reason": "x" * (ranking.MAX_REASON_LENGTH + 1),
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


@pytest.mark.parametrize(
    "result",
    [
        _decision(0),
        _decision(1, score=-1),
        _decision(1, score=101),
        _decision(1, reason="\n\t"),
        _decision(1, reason="x" * (ranking.MAX_REASON_LENGTH + 1)),
    ],
)
def test_post_parse_bounds_raise_typed_invalid_output(
    result: DecisionResult,
) -> None:
    """Classify every post-parse business-bound violation as invalid output."""
    with pytest.raises(RankingError) as caught:
        ranking._validate_result_ids(_response(result), [1])

    assert caught.value.kind is RankingErrorKind.INVALID_OUTPUT


def test_decision_transport_contract_is_strict() -> None:
    """Reject extra fields, coerced values, and invalid decision enums."""
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
        DecisionResult.model_validate(
            {
                "domain_id": 1,
                "criticality_score": "10",
                "decision": "ignore",
                "reason": "Healthy.",
            }
        )
    with pytest.raises(ValidationError):
        _decision(1, decision="invalid")


@pytest.mark.parametrize("shape", ["empty", "duplicate", "missing", "extra"])
def test_result_id_mismatches_fall_back_for_every_loaded_domain(
    monkeypatch: pytest.MonkeyPatch,
    db_session: Session,
    shape: str,
) -> None:
    """Reject duplicate, missing, or unexpected result IDs as one bad batch."""
    first = _add_domain(db_session, name="one.example.com")
    second = _add_domain(db_session, name="two.example.com")
    if shape == "empty":
        parsed = _response()
    elif shape == "duplicate":
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


def test_model_result_count_over_limit_falls_back(
    monkeypatch: pytest.MonkeyPatch, db_session: Session
) -> None:
    """Reject a structured response exceeding the model-result ceiling."""
    domain = _add_domain(db_session)
    parsed = _response(
        *(
            _decision(domain.id)
            for _ in range(ranking.MAX_DOMAINS + 1)
        )
    )
    _install_client(monkeypatch, [parsed])

    result = rank_domains([domain.id])[0]

    assert result.decision == "flag_for_review"
    assert result.criticality_score == ranking.FALLBACK_SCORE


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

    results = rank_domains([domain.id])

    assert len(results) == 1
    _assert_model_result(
        results[0],
        domain_id=domain.id,
        score=88,
        decision="auto_renew",
    )
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

    results = rank_domains([domain.id])

    assert len(results) == 1
    _assert_model_result(
        results[0],
        domain_id=domain.id,
        score=50,
        decision="flag_for_review",
    )
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
    _assert_model_result(
        results[1],
        domain_id=domain.id,
        score=12,
        decision="ignore",
    )
    assert [item["domain_id"] for item in _prompt_domains(client)] == [domain.id]


def test_model_reason_is_normalized_after_structured_parse(
    monkeypatch: pytest.MonkeyPatch, db_session: Session
) -> None:
    """Normalize printable model reasons after the SDK returns typed output."""
    domain = _add_domain(db_session)
    parsed = _response(
        _decision(
            domain.id,
            reason="  Observed facts\nrequire\tmanual review.  ",
        )
    )
    _install_client(monkeypatch, [parsed])

    result = rank_domains([domain.id])[0]

    assert result.reason == "Observed facts require manual review."


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


def test_history_with_equal_timestamps_uses_descending_id_tiebreaker(
    monkeypatch: pytest.MonkeyPatch, db_session: Session
) -> None:
    """Order equal-time history deterministically by newest decision ID."""
    domain = _add_domain(db_session)
    created_at = datetime.now(UTC)
    lower_id = AgentDecision(
        domain_id=domain.id,
        criticality_score=20,
        decision="ignore",
        reason="Lower decision ID.",
        created_at=created_at,
    )
    higher_id = AgentDecision(
        domain_id=domain.id,
        criticality_score=80,
        decision="flag_for_review",
        reason="Higher decision ID.",
        created_at=created_at,
    )
    db_session.add(lower_id)
    db_session.flush()
    db_session.add(higher_id)
    db_session.commit()
    assert higher_id.id > lower_id.id
    client = _install_client(monkeypatch, [_response(_decision(domain.id))])

    rank_domains([domain.id])

    history = _prompt_domains(client)[0]["recent_decisions"]
    assert [item["reason"] for item in history] == [
        "Higher decision ID.",
        "Lower decision ID.",
    ]
    assert all(
        set(item) == {"score", "decision", "reason", "created_at"}
        for item in history
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
    assert call["model"] == ranking.DEFAULT_MODEL
    assert call["model"] == "gpt-4o"
    assert call["text_format"] is RankingResponse
    assert call["max_output_tokens"] == ranking._output_token_budget(1)
    assert call["store"] is False


def test_full_batch_receives_sufficient_derived_output_budget(
    monkeypatch: pytest.MonkeyPatch, db_session: Session
) -> None:
    """Pass approximately 4K output tokens for a full 20-result batch."""
    domains = [
        _add_domain(db_session, name=f"batch-{index}.example.com")
        for index in range(ranking.MAX_DOMAINS)
    ]
    parsed = _response(
        *(
            _decision(domain.id, score=index, decision="ignore")
            for index, domain in enumerate(domains)
        )
    )
    client = _install_client(monkeypatch, [parsed])

    results = rank_domains([domain.id for domain in domains])

    assert len(results) == ranking.MAX_DOMAINS
    budget = client.responses.calls[0]["max_output_tokens"]
    assert budget == ranking._output_token_budget(ranking.MAX_DOMAINS)
    assert 4_000 <= budget <= 4_096


def test_output_token_budget_scales_and_never_exceeds_ceiling() -> None:
    """Scale small batches proportionally under the configured hard cap."""
    one_result = ranking._output_token_budget(1)
    full_batch = ranking._output_token_budget(ranking.MAX_DOMAINS)

    assert one_result < full_batch
    assert full_batch == ranking.MAX_OUTPUT_TOKEN_CEILING
    assert (
        ranking._output_token_budget(ranking.MAX_REQUEST_ITEMS * 10)
        == ranking.MAX_OUTPUT_TOKEN_CEILING
    )


def test_openai_generated_schema_omits_unsupported_constraint_keywords() -> None:
    """Inspect the real SDK strict-schema conversion used by responses.parse."""
    response_format = type_to_response_format_param(RankingResponse)
    assert isinstance(response_format, dict)
    json_schema = response_format["json_schema"]
    assert json_schema["strict"] is True
    schema = json_schema["schema"]
    unsupported = {
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
    }

    assert _schema_keys(schema).isdisjoint(unsupported)


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
