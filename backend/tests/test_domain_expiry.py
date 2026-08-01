"""Unit tests for IANA-bootstrapped RDAP domain expiration lookup."""

from collections.abc import Callable
from datetime import date

import httpx
import pytest

from backend.detection.domain_expiry import (
    DomainLookupError,
    DomainLookupErrorKind,
    get_domain_expiry,
)

BOOTSTRAP_PAYLOAD = {
    "services": [
        [["org"], ["https://rdap.publicinterestregistry.org/rdap/"]],
        [["com", "net"], ["https://rdap.verisign.com/com/v1/"]],
    ]
}


def _response(status_code: int, payload: object) -> httpx.Response:
    """Build an HTTPX response with the request metadata it requires."""
    request = httpx.Request("GET", "https://rdap.test/resource")
    return httpx.Response(status_code, json=payload, request=request)


def _mock_responses(
    monkeypatch: pytest.MonkeyPatch, responses: list[httpx.Response]
) -> None:
    """Replace HTTPX network calls with an ordered response sequence."""
    remaining = iter(responses)

    def fake_get(_: str, **__: object) -> httpx.Response:
        return next(remaining)

    monkeypatch.setattr(httpx, "get", fake_get)


def _domain_payload(expiration: str) -> dict[str, object]:
    """Return representative RDAP data with expiration after another event."""
    return {
        "events": [
            {"eventAction": "registration", "eventDate": "1995-08-14T04:00:00Z"},
            {"eventAction": "expiration", "eventDate": expiration},
        ],
        "status": ["active", "client transfer prohibited"],
        "entities": [
            {
                "roles": ["registrar"],
                "vcardArray": [
                    "vcard",
                    [["fn", {}, "text", "Example Registrar, Inc."]],
                ],
            }
        ],
    }


def test_near_expiry_domain_is_normalized_and_parsed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A near expiration event is found without depending on list position."""
    _mock_responses(
        monkeypatch,
        [
            _response(200, BOOTSTRAP_PAYLOAD),
            _response(200, _domain_payload("2026-08-12T04:00:00Z")),
        ],
    )

    result = get_domain_expiry(" Example.COM. ")

    assert result.domain == "example.com"
    assert result.expiry_date == date(2026, 8, 12)
    assert result.registrar == "Example Registrar, Inc."
    assert result.raw_status == ("active", "client transfer prohibited")


def test_far_future_expiry_is_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A far-future expiration timestamp is returned as a date."""
    _mock_responses(
        monkeypatch,
        [
            _response(200, BOOTSTRAP_PAYLOAD),
            _response(200, _domain_payload("2035-11-30T23:59:59+00:00")),
        ],
    )

    result = get_domain_expiry("long-lived.com")

    assert result.expiry_date == date(2035, 11, 30)


def test_domain_not_found_is_classified(monkeypatch: pytest.MonkeyPatch) -> None:
    """An authoritative RDAP 404 becomes a not-found domain error."""
    _mock_responses(
        monkeypatch,
        [_response(200, BOOTSTRAP_PAYLOAD), _response(404, {"errorCode": 404})],
    )

    with pytest.raises(DomainLookupError) as raised:
        get_domain_expiry("missing-example.com")

    assert raised.value.kind is DomainLookupErrorKind.NOT_FOUND
    assert raised.value.domain == "missing-example.com"


@pytest.mark.parametrize(
    "failure_factory",
    [
        lambda request: httpx.ReadTimeout("timed out", request=request),
        lambda request: httpx.ConnectError("unreachable", request=request),
    ],
    ids=["timeout", "transport"],
)
def test_provider_failure_is_classified(
    monkeypatch: pytest.MonkeyPatch,
    failure_factory: Callable[[httpx.Request], httpx.TransportError],
) -> None:
    """Timeout and transport failures become provider-unavailable errors."""
    request = httpx.Request("GET", "https://data.iana.org/rdap/dns.json")

    def fail_get(_: str, **__: object) -> httpx.Response:
        raise failure_factory(request)

    monkeypatch.setattr(httpx, "get", fail_get)

    with pytest.raises(DomainLookupError) as raised:
        get_domain_expiry("example.com")

    assert raised.value.kind is DomainLookupErrorKind.PROVIDER_UNAVAILABLE


def test_missing_expiration_event_is_malformed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A response without a usable expiration event is rejected."""
    malformed_payload = {
        "events": [{"eventAction": "registration", "eventDate": "2020-01-01T00:00:00Z"}],
        "status": ["active"],
    }
    _mock_responses(
        monkeypatch,
        [_response(200, BOOTSTRAP_PAYLOAD), _response(200, malformed_payload)],
    )

    with pytest.raises(DomainLookupError) as raised:
        get_domain_expiry("example.com")

    assert raised.value.kind is DomainLookupErrorKind.MALFORMED_RESPONSE


@pytest.mark.parametrize(
    "invalid_domain",
    [
        "",
        "https://example.com",
        "example.com/path",
        "example.com:443",
        "127.0.0.1",
        "*.example.com",
        "bad_domain.com",
        "localhost",
        "example.com..",
    ],
)
def test_invalid_domain_is_rejected_without_network_call(
    monkeypatch: pytest.MonkeyPatch, invalid_domain: str
) -> None:
    """Invalid input is rejected before any RDAP request is attempted."""

    def unexpected_get(_: str, **__: object) -> httpx.Response:
        pytest.fail("Invalid domain input must not trigger network access")

    monkeypatch.setattr(httpx, "get", unexpected_get)

    with pytest.raises(DomainLookupError) as raised:
        get_domain_expiry(invalid_domain)

    assert raised.value.kind is DomainLookupErrorKind.INVALID_DOMAIN
