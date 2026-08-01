"""Deterministic unit tests for renewal-coverage policy."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from backend.agent import policy
from backend.agent.policy import MandateCoverage, RenewalQuote, apply_renewal_policy
from backend.agent.ranking import DecisionResult, MAX_REASON_LENGTH

NOW = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


def _recommendation(
    decision: str = "auto_renew",
) -> DecisionResult:
    """Build one fixed valid model recommendation."""
    return DecisionResult(
        domain_id=7,
        criticality_score=91,
        decision=decision,  # type: ignore[arg-type]
        reason="Domain expiry is imminent.",
    )


def _quote(**changes: object) -> RenewalQuote:
    """Build one fixed fresh server-derived quote."""
    quote = RenewalQuote(
        domain_id=7,
        merchant_name="Example Registrar",
        merchant_url="https://registrar.example/renew",
        merchant_country="US",
        amount=Decimal("18.00"),
        currency="USD",
        observed_at=NOW,
    )
    return replace(quote, **changes)


def _mandate(**changes: object) -> MandateCoverage:
    """Build one fixed fully covering sanitized mandate."""
    mandate = MandateCoverage(
        record_id=11,
        domain_id=7,
        merchant_name="Example Registrar",
        merchant_url="https://registrar.example/renew/",
        merchant_country="US",
        cap_amount=Decimal("25.00"),
        currency="USD",
        frequency="yearly",
        status="active",
        valid_until=NOW + timedelta(days=30),
        created_at=NOW - timedelta(days=1),
    )
    return replace(mandate, **changes)


def _apply(
    *,
    recommendation: DecisionResult | None = None,
    quote: RenewalQuote | None = None,
    mandates: tuple[MandateCoverage, ...] | None = None,
) -> DecisionResult:
    """Apply policy with fully covered defaults."""
    return apply_renewal_policy(
        recommendation or _recommendation(),
        quote=quote if quote is not None else _quote(),
        mandates=mandates if mandates is not None else (_mandate(),),
        evaluated_at=NOW,
    )


@pytest.mark.parametrize("decision", ["ignore", "flag_for_review"])
def test_non_auto_recommendation_is_never_upgraded(decision: str) -> None:
    """Ignore and manual-review model results remain unchanged."""
    recommendation = _recommendation(decision)

    result = apply_renewal_policy(
        recommendation,
        quote=None,
        mandates=(),
        evaluated_at=NOW,
    )

    assert result == recommendation


def test_fully_covered_auto_renew_is_preserved() -> None:
    """One independently complete mandate retains auto-renewal eligibility."""
    result = _apply()

    assert result.decision == "auto_renew"
    assert result.reason == "Domain expiry is imminent."


def test_canonical_merchant_urls_match() -> None:
    """HTTPS host casing, default port, and trailing slash canonicalize equally."""
    mandate = _mandate(merchant_url="https://REGISTRAR.EXAMPLE:443/renew/")

    result = _apply(mandates=(mandate,))

    assert result.decision == "auto_renew"


def test_missing_mandate_downgrades() -> None:
    """Absent coverage cannot retain an auto-renew recommendation."""
    result = _apply(mandates=())

    assert result.decision == "flag_for_review"
    assert "No mandate" in result.reason


def test_missing_quote_downgrades() -> None:
    """Missing server-derived price evidence cannot retain auto-renewal."""
    result = apply_renewal_policy(
        _recommendation(),
        quote=None,
        mandates=(_mandate(),),
        evaluated_at=NOW,
    )

    assert result.decision == "flag_for_review"
    assert "quote" in result.reason


def test_quote_for_another_domain_downgrades() -> None:
    """A quote bound to another domain cannot prove requested coverage."""
    result = _apply(quote=_quote(domain_id=8))

    assert result.decision == "flag_for_review"
    assert "quote" in result.reason


@pytest.mark.parametrize(
    ("changes", "reason_fragment"),
    [
        ({"status": "inactive"}, "not active"),
        ({"valid_until": NOW}, "expired"),
        ({"valid_until": None}, "unproven"),
        ({"domain_id": 8}, "different domain"),
        ({"merchant_name": "Other Registrar"}, "merchant name"),
        ({"merchant_url": "https://other.example/renew"}, "merchant URL"),
        ({"merchant_country": "GB"}, "merchant country"),
        ({"frequency": "monthly"}, "not yearly"),
        ({"currency": "EUR"}, "currency"),
    ],
)
def test_invalid_mandate_condition_downgrades(
    changes: dict[str, object], reason_fragment: str
) -> None:
    """Every required mandate binding independently fails closed."""
    result = _apply(mandates=(_mandate(**changes),))

    assert result.decision == "flag_for_review"
    assert reason_fragment in result.reason


def test_stale_quote_downgrades() -> None:
    """A quote older than the freshness window cannot prove coverage."""
    stale = _quote(observed_at=NOW - policy.MAX_QUOTE_AGE - timedelta(seconds=1))

    result = _apply(quote=stale)

    assert result.decision == "flag_for_review"
    assert "stale" in result.reason


def test_future_dated_quote_downgrades() -> None:
    """A quote beyond bounded clock skew cannot prove current price."""
    future = _quote(
        observed_at=NOW + policy.MAX_QUOTE_FUTURE_SKEW + timedelta(seconds=1)
    )

    result = _apply(quote=future)

    assert result.decision == "flag_for_review"
    assert "future" in result.reason


@pytest.mark.parametrize("amount", [Decimal("0"), Decimal("-0.01")])
def test_non_positive_quote_downgrades(amount: Decimal) -> None:
    """Zero and negative server amounts cannot retain auto-renewal."""
    result = _apply(quote=_quote(amount=amount))

    assert result.decision == "flag_for_review"
    assert "amount" in result.reason


def test_quote_above_cap_downgrades() -> None:
    """A current price above the selected mandate cap fails closed."""
    result = _apply(mandates=(_mandate(cap_amount=Decimal("17.99")),))

    assert result.decision == "flag_for_review"
    assert "exceeds" in result.reason


def test_quote_equal_to_cap_remains_covered() -> None:
    """Decimal equality at the exact cap remains independently covered."""
    result = _apply(mandates=(_mandate(cap_amount=Decimal("18.00")),))

    assert result.decision == "auto_renew"


def test_partial_mandates_cannot_combine_coverage() -> None:
    """Fields from separate incomplete mandates never manufacture coverage."""
    wrong_merchant = _mandate(record_id=20, merchant_name="Other Registrar")
    insufficient_cap = _mandate(record_id=19, cap_amount=Decimal("10.00"))

    result = _apply(mandates=(insufficient_cap, wrong_merchant))

    assert result.decision == "flag_for_review"


def test_one_valid_mandate_among_invalid_candidates_proves_coverage() -> None:
    """Any one independently complete candidate can preserve auto-renewal."""
    invalid = _mandate(record_id=20, currency="EUR")
    valid = _mandate(record_id=19)

    result = _apply(mandates=(invalid, valid))

    assert result.decision == "auto_renew"


def test_mandate_failure_selection_is_deterministic() -> None:
    """Newest timestamp then highest record ID controls stable failure reason."""
    older = _mandate(
        record_id=99,
        merchant_country="GB",
        created_at=NOW - timedelta(days=2),
    )
    newer = _mandate(
        record_id=1,
        currency="EUR",
        created_at=NOW - timedelta(hours=1),
    )

    first = _apply(mandates=(older, newer))
    second = _apply(mandates=(newer, older))

    assert first.reason == second.reason
    assert "currency" in first.reason


def test_decimal_comparison_avoids_binary_float_rounding() -> None:
    """Exact Decimal arithmetic covers a mathematically equal computed cap."""
    exact_cap = Decimal("0.10") + Decimal("0.20")
    result = _apply(
        quote=_quote(amount=Decimal("0.30")),
        mandates=(_mandate(cap_amount=exact_cap),),
    )

    assert result.decision == "auto_renew"
    assert exact_cap == Decimal("0.30")


def test_float_money_is_rejected_instead_of_compared() -> None:
    """Runtime float values fail closed at the Decimal-only policy boundary."""
    unsafe = _mandate(cap_amount=18.0)  # type: ignore[arg-type]

    result = _apply(mandates=(unsafe,))

    assert result.decision == "flag_for_review"
    assert "cap" in result.reason


def test_downgrade_reason_is_sanitized_nonempty_and_bounded() -> None:
    """Application-controlled downgrade text cannot exceed the response bound."""
    result = policy._downgrade(
        _recommendation(),
        "  Manual\nreview\t" + "x" * (MAX_REASON_LENGTH * 2),
    )

    assert result.decision == "flag_for_review"
    assert result.reason.startswith("Manual review")
    assert result.reason.isprintable()
    assert 0 < len(result.reason) <= MAX_REASON_LENGTH
