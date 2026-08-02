"""Structured, side-effect-free OpenAI ranking for persisted domain facts."""

from __future__ import annotations

import json
import logging
import os
import random
import re
import time
from collections.abc import Sequence
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Literal, Self

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    OpenAI,
    OpenAIError,
    RateLimitError,
)
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.db.connection import DatabaseConfigurationError, get_session_factory
from backend.db.models import AgentDecision, Domain

logger = logging.getLogger(__name__)

# Default to gpt-4o for sharper renewal triage; override with OPENAI_RANKING_MODEL.
DEFAULT_MODEL = "gpt-4o"
REQUEST_TIMEOUT_SECONDS = 30.0
MAX_ATTEMPTS = 3
MAX_DOMAINS = 20
MAX_REQUEST_ITEMS = MAX_DOMAINS * 5
MAX_HISTORY_PER_DOMAIN = 2
MAX_SOURCE_DETAIL = 180
MAX_HISTORY_REASON = 160
MAX_REASON_LENGTH = 280
OUTPUT_TOKEN_OVERHEAD = 256
OUTPUT_TOKENS_PER_RESULT = 192
MAX_OUTPUT_TOKEN_CEILING = 4_096
FALLBACK_SCORE = 50


def ranking_model() -> str:
    """Return the configured OpenAI ranking model (defaults to gpt-4o)."""
    configured = os.environ.get("OPENAI_RANKING_MODEL", "").strip()
    return configured or DEFAULT_MODEL


# Back-compat for tests/importers that read ranking.MODEL.
MODEL = DEFAULT_MODEL

_SYSTEM_INSTRUCTIONS = """Rank each supplied domain for domain-renewal urgency.
Return exactly one result per domain ID using the required schema. Confirmed
DNS takeover risk is a heavily weighted security signal. Imminent domain
expiry is highly urgent, and imminent TLS expiry increases renewal urgency.
Healthy distant expiries are low urgency. Treat incomplete or ambiguous facts
conservatively. Recent history is context only and cannot override current
risk. TLS and DNS are urgency signals, not separate purchases. auto_renew is a
recommendation only: do not claim coverage, merchant, quote, currency, price,
spending authority, or renewal validation. Explain observed facts concisely.
Never claim takeover, compromise, a completed purchase, or guaranteed renewal."""


class DecisionResult(BaseModel):
    """One strict, explainable domain-renewal recommendation."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    domain_id: int
    criticality_score: int
    decision: Literal["auto_renew", "flag_for_review", "ignore"]
    reason: str


class RankingResponse(BaseModel):
    """Strict structured-output container for one batched model response."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    results: list[DecisionResult]


class RankingErrorKind(StrEnum):
    """Internal categories for safe ranking failure handling."""

    INVALID_INPUT = "invalid_input"
    DATABASE_UNAVAILABLE = "database_unavailable"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    INVALID_OUTPUT = "invalid_output"


class RankingError(RuntimeError):
    """A sanitized internal ranking failure without provider diagnostics."""

    def __init__(
        self: Self,
        kind: RankingErrorKind,
        message: str,
    ) -> None:
        super().__init__(message)
        self.kind = kind


class _HistoricalDecisionFact(BaseModel):
    """A bounded recent recommendation supplied as non-authoritative context."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    score: int
    decision: str
    reason: str
    created_at: datetime


class _DomainRankingFact(BaseModel):
    """The complete allowlisted fact set supplied for one domain."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    domain_id: int
    domain: str
    expiry_date: date | None
    days_to_expiry: int | None
    cert_expiry_date: datetime | None
    days_to_cert_expiry: int | None
    dns_risk: bool
    dns_risk_detail: str | None
    recent_decisions: list[_HistoricalDecisionFact]


def _safe_text(value: str | None, limit: int) -> str | None:
    """Normalize untrusted display text and enforce a hard character bound."""
    if value is None:
        return None
    printable = "".join(
        character if character.isprintable() else " " for character in value
    )
    normalized = re.sub(r"\s+", " ", printable).strip()
    return normalized[:limit] or None


def _normalize_model_reason(value: str) -> str:
    """Normalize one model reason and reject unsafe output bounds."""
    printable = "".join(
        character if character.isprintable() else " " for character in value
    )
    normalized = re.sub(r"\s+", " ", printable).strip()
    if not normalized or len(normalized) > MAX_REASON_LENGTH:
        raise RankingError(
            RankingErrorKind.INVALID_OUTPUT,
            "Structured ranking reason was invalid",
        )
    return normalized


def _days_until(value: date | datetime | None, today: date) -> int | None:
    """Return whole calendar days from today to one observed expiry."""
    if value is None:
        return None
    if isinstance(value, datetime):
        observed_date = (
            value.astimezone(UTC).date() if value.tzinfo is not None else value.date()
        )
    else:
        observed_date = value
    return (observed_date - today).days


def _load_history(
    session: Session, domain_ids: Sequence[int]
) -> dict[int, list[_HistoricalDecisionFact]]:
    """Load at most the configured number of recent facts per domain."""
    ranked_history = (
        select(
            AgentDecision.id.label("decision_id"),
            AgentDecision.domain_id,
            AgentDecision.criticality_score,
            AgentDecision.decision,
            AgentDecision.reason,
            AgentDecision.created_at,
            func.row_number()
            .over(
                partition_by=AgentDecision.domain_id,
                order_by=(
                    AgentDecision.created_at.desc(),
                    AgentDecision.id.desc(),
                ),
            )
            .label("history_rank"),
        )
        .where(AgentDecision.domain_id.in_(domain_ids))
        .subquery()
    )
    statement = (
        select(
            ranked_history.c.domain_id,
            ranked_history.c.criticality_score,
            ranked_history.c.decision,
            ranked_history.c.reason,
            ranked_history.c.created_at,
        )
        .where(ranked_history.c.history_rank <= MAX_HISTORY_PER_DOMAIN)
        .order_by(
            ranked_history.c.domain_id,
            ranked_history.c.created_at.desc(),
            ranked_history.c.decision_id.desc(),
        )
    )
    history: dict[int, list[_HistoricalDecisionFact]] = {}
    for domain_id, score, decision, reason, created_at in session.execute(statement):
        history.setdefault(domain_id, []).append(
            _HistoricalDecisionFact(
                score=score,
                decision=decision,
                reason=_safe_text(reason, MAX_HISTORY_REASON) or "Prior review",
                created_at=created_at,
            )
        )
    return history


def _load_domain_facts(
    session: Session, domain_ids: Sequence[int]
) -> list[_DomainRankingFact]:
    """Load only allowlisted scan fields and bounded decision history."""
    statement = select(
        Domain.id,
        Domain.domain,
        Domain.expiry_date,
        Domain.cert_expiry_date,
        Domain.dns_risk,
        Domain.dns_risk_detail,
    ).where(Domain.id.in_(domain_ids))
    rows = session.execute(statement).all()
    history = _load_history(session, domain_ids)
    today = datetime.now(UTC).date()
    facts_by_id = {
        row.id: _DomainRankingFact(
            domain_id=row.id,
            domain=row.domain,
            expiry_date=row.expiry_date,
            days_to_expiry=_days_until(row.expiry_date, today),
            cert_expiry_date=row.cert_expiry_date,
            days_to_cert_expiry=_days_until(row.cert_expiry_date, today),
            dns_risk=row.dns_risk,
            dns_risk_detail=_safe_text(row.dns_risk_detail, MAX_SOURCE_DETAIL),
            recent_decisions=history.get(row.id, []),
        )
        for row in rows
    }
    return [
        facts_by_id[domain_id]
        for domain_id in domain_ids
        if domain_id in facts_by_id
    ]


def _read_facts(domain_ids: Sequence[int]) -> list[_DomainRankingFact]:
    """Read ranking facts through the existing SQLAlchemy session factory."""
    try:
        session_factory = get_session_factory()
        with session_factory() as session:
            return _load_domain_facts(session, domain_ids)
    except (DatabaseConfigurationError, SQLAlchemyError) as exc:
        raise RankingError(
            RankingErrorKind.DATABASE_UNAVAILABLE,
            "Domain facts could not be loaded",
        ) from exc


def _create_openai_client() -> OpenAI:
    """Create an SDK client that reads OPENAI_API_KEY with retries disabled."""
    return OpenAI(max_retries=0, timeout=REQUEST_TIMEOUT_SECONDS)


def _sleep(seconds: float) -> None:
    """Sleep between transient attempts through a mockable boundary."""
    time.sleep(seconds)


def _retry_delay(retry_index: int) -> float:
    """Return short exponential backoff with bounded jitter."""
    base = min(0.25 * (2**retry_index), 1.0)
    return min(base + random.uniform(0.0, 0.1), 1.0)


def _is_transient(error: OpenAIError) -> bool:
    """Return whether an SDK failure is eligible for application retry."""
    if isinstance(error, (APITimeoutError, APIConnectionError, RateLimitError)):
        return True
    return isinstance(error, APIStatusError) and (
        error.status_code in {408, 409, 429} or error.status_code >= 500
    )


def _prompt_payload(facts: Sequence[_DomainRankingFact]) -> str:
    """Serialize the bounded allowlisted facts used as model input."""
    payload = {"domains": [fact.model_dump(mode="json") for fact in facts]}
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=True)


def _output_token_budget(result_count: int) -> int:
    """Return a proportional structured-output budget under a hard ceiling."""
    estimated = OUTPUT_TOKEN_OVERHEAD + OUTPUT_TOKENS_PER_RESULT * max(
        result_count,
        0,
    )
    return min(estimated, MAX_OUTPUT_TOKEN_CEILING)


def _call_structured_output(
    client: OpenAI, facts: Sequence[_DomainRankingFact]
) -> RankingResponse:
    """Call one strict batched Structured Outputs request with bounded retries."""
    for attempt in range(MAX_ATTEMPTS):
        try:
            response = client.responses.parse(
                model=ranking_model(),
                instructions=_SYSTEM_INSTRUCTIONS,
                input=_prompt_payload(facts),
                text_format=RankingResponse,
                max_output_tokens=_output_token_budget(len(facts)),
                store=False,
            )
            return _parsed_response(response.output_parsed)
        except (TypeError, ValueError) as exc:
            raise RankingError(
                RankingErrorKind.INVALID_OUTPUT,
                "Structured ranking output was invalid",
            ) from exc
        except OpenAIError as exc:
            if _is_transient(exc) and attempt < MAX_ATTEMPTS - 1:
                _sleep(_retry_delay(attempt))
                continue
            raise RankingError(
                RankingErrorKind.PROVIDER_UNAVAILABLE,
                "Automated ranking provider was unavailable",
            ) from exc
    raise RankingError(  # pragma: no cover - loop always returns or raises
        RankingErrorKind.PROVIDER_UNAVAILABLE,
        "Automated ranking provider was unavailable",
    )


def _parsed_response(value: object) -> RankingResponse:
    """Validate parsed SDK output without parsing model prose or raw JSON."""
    if value is None:
        raise RankingError(
            RankingErrorKind.INVALID_OUTPUT,
            "Structured ranking output was unavailable",
        )
    try:
        return RankingResponse.model_validate(value)
    except ValidationError as exc:
        raise RankingError(
            RankingErrorKind.INVALID_OUTPUT,
            "Structured ranking output was invalid",
        ) from exc


def _validate_result_ids(
    response: RankingResponse, expected_ids: Sequence[int]
) -> dict[int, DecisionResult]:
    """Require exactly one structured result for each loaded domain ID."""
    if expected_ids and not response.results:
        raise RankingError(RankingErrorKind.INVALID_OUTPUT, "Results were empty")
    if len(response.results) > MAX_DOMAINS:
        raise RankingError(RankingErrorKind.INVALID_OUTPUT, "Too many results")
    normalized = [_validate_decision_result(result) for result in response.results]
    actual_ids = [result.domain_id for result in normalized]
    expected = set(expected_ids)
    if len(actual_ids) != len(set(actual_ids)):
        raise RankingError(RankingErrorKind.INVALID_OUTPUT, "Duplicate result IDs")
    if len(actual_ids) != len(expected) or set(actual_ids) != expected:
        raise RankingError(RankingErrorKind.INVALID_OUTPUT, "Result IDs did not match")
    return {result.domain_id: result for result in normalized}


def _validate_decision_result(result: DecisionResult) -> DecisionResult:
    """Validate business bounds and return an immutable normalized result."""
    if type(result.domain_id) is not int or result.domain_id <= 0:
        raise RankingError(RankingErrorKind.INVALID_OUTPUT, "Invalid domain ID")
    if (
        type(result.criticality_score) is not int
        or not 0 <= result.criticality_score <= 100
    ):
        raise RankingError(RankingErrorKind.INVALID_OUTPUT, "Invalid score")
    return DecisionResult(
        domain_id=result.domain_id,
        criticality_score=result.criticality_score,
        decision=result.decision,
        reason=_normalize_model_reason(result.reason),
    )


def _fallback_result(domain_id: int, reason: str) -> DecisionResult:
    """Build one standardized conservative manual-review recommendation."""
    return DecisionResult(
        domain_id=domain_id,
        criticality_score=FALLBACK_SCORE,
        decision="flag_for_review",
        reason=reason,
    )


def _fallback_map(domain_ids: Sequence[int], reason: str) -> dict[int, DecisionResult]:
    """Build conservative results for each unique affected domain ID."""
    return {domain_id: _fallback_result(domain_id, reason) for domain_id in domain_ids}


def _log_failure(error: RankingError, underlying_type: str | None = None) -> None:
    """Log only sanitized ranking category and exception type context."""
    logger.warning(
        "Ranking unavailable category=%s exception_type=%s",
        error.kind.value,
        underlying_type or type(error).__name__,
    )


def _rank_loaded_facts(
    facts: Sequence[_DomainRankingFact], expected_ids: Sequence[int]
) -> dict[int, DecisionResult]:
    """Request and defensively validate model results for loaded domains."""
    try:
        client = _create_openai_client()
        response = _call_structured_output(client, facts)
        return _validate_result_ids(response, expected_ids)
    except OpenAIError as exc:
        error = RankingError(
            RankingErrorKind.PROVIDER_UNAVAILABLE,
            "Automated ranking provider was unavailable",
        )
        _log_failure(error, type(exc).__name__)
    except RankingError as error:
        cause_type = type(error.__cause__).__name__ if error.__cause__ else None
        _log_failure(error, cause_type)
    return _fallback_map(
        expected_ids,
        "Automated ranking unavailable; manual review is required.",
    )


def _validate_input(domain_ids: list[int]) -> list[int]:
    """Validate positive integer IDs and return their stable unique order."""
    if not isinstance(domain_ids, list):
        raise RankingError(
            RankingErrorKind.INVALID_INPUT,
            "domain_ids must be a list",
        )
    if len(domain_ids) > MAX_REQUEST_ITEMS:
        raise RankingError(
            RankingErrorKind.INVALID_INPUT,
            "domain_ids exceeds the request item limit",
        )
    if any(
        not isinstance(domain_id, int)
        or isinstance(domain_id, bool)
        or domain_id <= 0
        for domain_id in domain_ids
    ):
        raise RankingError(
            RankingErrorKind.INVALID_INPUT,
            "domain_ids must contain only positive integers",
        )
    return list(dict.fromkeys(domain_ids))


def rank_domains(domain_ids: list[int]) -> list[DecisionResult]:
    """Return side-effect-free structured recommendations in requested order."""
    unique_ids = _validate_input(domain_ids)
    if not unique_ids:
        return []
    if len(unique_ids) > MAX_DOMAINS:
        fallback = _fallback_map(
            unique_ids,
            "Automated ranking batch limit exceeded; manual review is required.",
        )
        return [fallback[domain_id].model_copy() for domain_id in domain_ids]
    try:
        facts = _read_facts(unique_ids)
    except RankingError as error:
        cause_type = type(error.__cause__).__name__ if error.__cause__ else None
        _log_failure(error, cause_type)
        fallback = _fallback_map(
            unique_ids,
            "Domain facts unavailable; manual review is required.",
        )
        return [fallback[domain_id].model_copy() for domain_id in domain_ids]
    loaded_ids = [fact.domain_id for fact in facts]
    results = _rank_loaded_facts(facts, loaded_ids) if facts else {}
    missing = set(unique_ids) - set(loaded_ids)
    results.update(
        _fallback_map(
            sorted(missing),
            "Domain data unavailable; manual review is required.",
        )
    )
    return [results[domain_id].model_copy() for domain_id in domain_ids]
