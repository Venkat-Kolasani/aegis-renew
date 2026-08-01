"""Pure deterministic coverage policy for domain-renewal recommendations."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from urllib.parse import urlsplit, urlunsplit

from backend.agent.ranking import DecisionResult, MAX_REASON_LENGTH

MAX_QUOTE_AGE = timedelta(minutes=5)
MAX_QUOTE_FUTURE_SKEW = timedelta(seconds=30)


@dataclass(frozen=True, slots=True)
class RenewalQuote:
    """Current server-observed merchant quote used for coverage evaluation."""

    domain_id: int
    merchant_name: str
    merchant_url: str
    merchant_country: str
    amount: Decimal
    currency: str
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class MandateCoverage:
    """Sanitized non-secret mandate metadata needed by the pure policy."""

    record_id: int
    domain_id: int
    merchant_name: str
    merchant_url: str
    merchant_country: str
    cap_amount: Decimal
    currency: str
    frequency: str
    status: str
    valid_until: datetime | None
    created_at: datetime


def apply_renewal_policy(
    recommendation: DecisionResult,
    *,
    quote: RenewalQuote | None,
    mandates: Sequence[MandateCoverage],
    evaluated_at: datetime,
) -> DecisionResult:
    """Preserve or conservatively downgrade one ranking recommendation."""
    if recommendation.decision != "auto_renew":
        return recommendation.model_copy()

    quote_failure = _quote_failure(recommendation, quote, evaluated_at)
    if quote_failure is not None:
        return _downgrade(recommendation, quote_failure)
    if quote is None:  # pragma: no cover - covered by _quote_failure
        return _downgrade(recommendation, "Current quote is unavailable.")

    ordered = _ordered_mandates(mandates)
    if not ordered:
        return _downgrade(
            recommendation,
            "No mandate proves active renewal coverage; manual review is required.",
        )
    failures = [
        _mandate_failure(mandate, quote, evaluated_at) for mandate in ordered
    ]
    if any(failure is None for failure in failures):
        return recommendation.model_copy()
    return _downgrade(recommendation, failures[0] or "Coverage is incomplete.")


def _quote_failure(
    recommendation: DecisionResult,
    quote: RenewalQuote | None,
    evaluated_at: datetime,
) -> str | None:
    """Return the deterministic quote failure reason, if any."""
    evaluation_time = _aware_utc(evaluated_at)
    if evaluation_time is None:
        return "Policy evaluation time is invalid; manual review is required."
    if quote is None or quote.domain_id != recommendation.domain_id:
        return "Current server-derived renewal quote is unavailable."
    observed_at = _aware_utc(quote.observed_at)
    if observed_at is None:
        return "Current renewal quote has an invalid timestamp."
    if observed_at > evaluation_time + MAX_QUOTE_FUTURE_SKEW:
        return "Current renewal quote is dated too far in the future."
    if evaluation_time - observed_at > MAX_QUOTE_AGE:
        return "Current renewal quote is stale; manual review is required."
    if type(quote.amount) is not Decimal or quote.amount <= 0:
        return "Current renewal quote amount is invalid."
    if not _quote_identity_is_valid(quote):
        return "Current renewal quote has invalid merchant metadata."
    return None


def _quote_identity_is_valid(quote: RenewalQuote) -> bool:
    """Return whether quote identity fields are complete and canonicalizable."""
    return bool(
        _normalized_identity(quote.merchant_name)
        and _canonical_merchant_url(quote.merchant_url)
        and _normalized_country(quote.merchant_country)
        and _normalized_currency(quote.currency)
    )


def _mandate_failure(
    mandate: MandateCoverage,
    quote: RenewalQuote,
    evaluated_at: datetime,
) -> str | None:
    """Return why one mandate fails independently, or none when fully covered."""
    if mandate.domain_id != quote.domain_id:
        return "The selected mandate belongs to a different domain."
    if _normalized_identity(mandate.status) != "active":
        return "The selected mandate is not active."
    if not _merchant_name_matches(mandate, quote):
        return "The mandate merchant name does not match the current quote."
    if not _merchant_url_matches(mandate, quote):
        return "The mandate merchant URL does not match the current quote."
    if _normalized_country(mandate.merchant_country) != _normalized_country(
        quote.merchant_country
    ):
        return "The mandate merchant country does not match the current quote."
    if mandate.frequency != "yearly":
        return "The mandate frequency is not yearly."
    if _normalized_currency(mandate.currency) != _normalized_currency(quote.currency):
        return "The mandate currency does not match the current quote."
    validity_failure = _validity_failure(mandate, evaluated_at)
    if validity_failure is not None:
        return validity_failure
    if type(mandate.cap_amount) is not Decimal or mandate.cap_amount <= 0:
        return "The mandate cap is invalid."
    if quote.amount > mandate.cap_amount:
        return "The current renewal quote exceeds the mandate cap."
    return None


def _validity_failure(
    mandate: MandateCoverage, evaluated_at: datetime
) -> str | None:
    """Return why mandate time coverage is not proven, if applicable."""
    if mandate.valid_until is None:
        return "The mandate has no validity end time, so coverage is unproven."
    valid_until = _aware_utc(mandate.valid_until)
    evaluation_time = _aware_utc(evaluated_at)
    if valid_until is None or evaluation_time is None:
        return "The mandate validity timestamp is invalid."
    if valid_until <= evaluation_time:
        return "The mandate coverage has expired."
    return None


def _ordered_mandates(
    mandates: Sequence[MandateCoverage],
) -> list[MandateCoverage]:
    """Return mandates in stable newest-record-first evaluation order."""
    return sorted(mandates, key=_mandate_sort_key, reverse=True)


def _mandate_sort_key(mandate: MandateCoverage) -> tuple[float, int]:
    """Return a stable sortable key without trusting naive local time."""
    created_at = _aware_utc(mandate.created_at)
    timestamp = created_at.timestamp() if created_at is not None else float("-inf")
    return timestamp, mandate.record_id


def _merchant_name_matches(
    mandate: MandateCoverage, quote: RenewalQuote
) -> bool:
    """Compare normalized merchant display identities."""
    return _normalized_identity(mandate.merchant_name) == _normalized_identity(
        quote.merchant_name
    )


def _merchant_url_matches(
    mandate: MandateCoverage, quote: RenewalQuote
) -> bool:
    """Compare canonical HTTPS merchant URLs."""
    mandate_url = _canonical_merchant_url(mandate.merchant_url)
    quote_url = _canonical_merchant_url(quote.merchant_url)
    return mandate_url is not None and mandate_url == quote_url


def _canonical_merchant_url(value: str) -> str | None:
    """Canonicalize a credential-free HTTPS merchant identity URL."""
    try:
        parsed = urlsplit(value.strip())
        port = parsed.port
    except (AttributeError, TypeError, ValueError):
        return None
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        return None
    host = parsed.hostname.rstrip(".").lower()
    netloc = host if port in {None, 443} else f"{host}:{port}"
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit(("https", netloc, path, "", ""))


def _normalized_identity(value: str) -> str:
    """Normalize one non-secret identity string for deterministic comparison."""
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value).strip().casefold()


def _normalized_country(value: str) -> str | None:
    """Return a normalized two-letter country or none."""
    if not isinstance(value, str):
        return None
    normalized = value.strip().upper()
    return normalized if len(normalized) == 2 and normalized.isalpha() else None


def _normalized_currency(value: str) -> str | None:
    """Return a normalized three-letter currency or none."""
    if not isinstance(value, str):
        return None
    normalized = value.strip().upper()
    return normalized if len(normalized) == 3 and normalized.isalpha() else None


def _aware_utc(value: datetime) -> datetime | None:
    """Normalize an aware datetime to UTC and reject naive timestamps."""
    if not isinstance(value, datetime) or value.tzinfo is None:
        return None
    return value.astimezone(UTC)


def _downgrade(recommendation: DecisionResult, reason: str) -> DecisionResult:
    """Build one bounded deterministic manual-review decision."""
    normalized = _bounded_reason(reason)
    return DecisionResult(
        domain_id=recommendation.domain_id,
        criticality_score=recommendation.criticality_score,
        decision="flag_for_review",
        reason=normalized,
    )


def _bounded_reason(value: str) -> str:
    """Normalize and bound an application-controlled policy reason."""
    printable = "".join(
        character if character.isprintable() else " " for character in value
    )
    normalized = re.sub(r"\s+", " ", printable).strip()
    return normalized[:MAX_REASON_LENGTH].rstrip() or "Manual review is required."
