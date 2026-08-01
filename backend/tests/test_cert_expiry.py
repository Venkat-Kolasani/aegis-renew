"""Offline unit tests for certificate-expiration detection."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from backend.detection import cert_expiry
from backend.detection.cert_expiry import (
    CertExpiryResult,
    CertLookupError,
    CertLookupErrorKind,
    get_cert_expiry,
)


def _crt_response(payload: object) -> httpx.Response:
    """Build a successful mocked crt.sh response."""
    request = httpx.Request("GET", "https://crt.sh/?q=example.com&output=json")
    return httpx.Response(200, json=payload, request=request)


def _crt_row(
    not_after: str,
    *,
    names: str = "example.com",
    serial: str = "01",
    issuer: str = "C=US, O=Example CA, CN=Example Issuer",
    not_before: str = "2020-01-01T00:00:00Z",
) -> dict[str, object]:
    """Build a representative crt.sh certificate row."""
    return {
        "common_name": names.splitlines()[0],
        "name_value": names,
        "not_before": not_before,
        "not_after": not_after,
        "issuer_name": issuer,
        "serial_number": serial,
    }


def _mock_crt_payload(monkeypatch: pytest.MonkeyPatch, payload: object) -> None:
    """Mock crt.sh while leaving the direct TLS fallback independently testable."""
    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: _crt_response(payload))


def _fallback_result(domain: str = "example.com") -> CertExpiryResult:
    """Return a deterministic successful TLS fallback result."""
    return CertExpiryResult(
        domain=domain,
        not_after=datetime(2097, 1, 1, tzinfo=UTC),
        issuer="Fallback CA",
        source="tls",
    )


def test_latest_current_certificate_is_selected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Selection uses validity and not_after rather than crt.sh row order."""
    rows = [
        _crt_row("2095-01-01T00:00:00Z", serial="later"),
        _crt_row("2090-01-01T00:00:00Z", serial="earlier"),
        _crt_row(
            "2005-01-01T00:00:00Z",
            not_before="2000-01-01T00:00:00Z",
            serial="expired",
        ),
    ]
    _mock_crt_payload(monkeypatch, rows)

    result = get_cert_expiry(" Example.COM. ")

    assert result.domain == "example.com"
    assert result.not_after == datetime(2095, 1, 1, tzinfo=UTC)
    assert result.not_after.tzinfo is UTC
    assert result.source == "crt.sh"


def test_duplicate_crt_rows_are_deduplicated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Duplicate crt.sh rows produce one stable certificate candidate."""
    duplicate = _crt_row("2095-01-01T00:00:00Z", serial="duplicate")
    same_certificate = dict(duplicate)
    same_certificate["name_value"] = "example.com\nwww.example.com"
    _mock_crt_payload(monkeypatch, [duplicate, same_certificate])

    candidates = cert_expiry._fetch_crt_candidates("example.com")

    assert len(candidates) == 1


def test_unrelated_subdomain_certificate_is_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A later certificate for another host cannot replace an exact match."""
    rows = [
        _crt_row("2099-01-01T00:00:00Z", names="other.example.com", serial="other"),
        _crt_row("2098-01-01T00:00:00Z", names="*.example.com", serial="wildcard"),
        _crt_row("2092-01-01T00:00:00Z", names="example.com", serial="exact"),
    ]
    _mock_crt_payload(monkeypatch, rows)

    result = get_cert_expiry("example.com")

    assert result.not_after == datetime(2092, 1, 1, tzinfo=UTC)


def test_single_label_wildcard_covers_requested_hostname(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A wildcard covers one subdomain level but not unrelated names."""
    rows = [
        _crt_row("2094-01-01T00:00:00Z", names="*.example.com", serial="wildcard"),
        _crt_row("2099-01-01T00:00:00Z", names="*.other.com", serial="other"),
    ]
    _mock_crt_payload(monkeypatch, rows)

    result = get_cert_expiry("api.example.com")

    assert result.not_after == datetime(2094, 1, 1, tzinfo=UTC)


def test_latest_expired_certificate_is_used_when_all_are_expired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The least-old expired match is returned only when every match expired."""
    rows = [
        _crt_row(
            "2005-01-01T00:00:00Z",
            not_before="2000-01-01T00:00:00Z",
            serial="latest",
        ),
        _crt_row(
            "2001-01-01T00:00:00Z",
            not_before="1999-01-01T00:00:00Z",
            serial="older",
        ),
    ]
    _mock_crt_payload(monkeypatch, rows)

    result = get_cert_expiry("example.com")

    assert result.not_after == datetime(2005, 1, 1, tzinfo=UTC)
    assert result.source == "crt.sh"


def test_inverted_crt_date_range_is_rejected_and_uses_tls_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A certificate starting after it expires cannot become a candidate."""
    inverted = _crt_row(
        "2095-01-01T00:00:00Z",
        not_before="2096-01-01T00:00:00Z",
        serial="inverted",
    )

    assert cert_expiry._parse_crt_candidate(inverted, "example.com") is None

    _mock_crt_payload(monkeypatch, [inverted])
    monkeypatch.setattr(cert_expiry, "_tls_lookup", _fallback_result)

    result = get_cert_expiry("example.com")

    assert result.source == "tls"


@pytest.mark.parametrize(
    "payload",
    [[], {"unexpected": "object"}, [{"not_after": "not-a-date"}]],
)
def test_empty_or_malformed_crt_response_uses_tls_fallback(
    monkeypatch: pytest.MonkeyPatch, payload: object
) -> None:
    """Empty and malformed crt.sh responses trigger direct TLS."""
    _mock_crt_payload(monkeypatch, payload)
    monkeypatch.setattr(cert_expiry, "_tls_lookup", _fallback_result)

    result = get_cert_expiry("example.com")

    assert result.source == "tls"


def test_crt_timeout_uses_tls_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """A crt.sh timeout degrades to direct TLS instead of escaping."""
    request = httpx.Request("GET", "https://crt.sh/")

    def timeout(*_: object, **__: object) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    monkeypatch.setattr(httpx, "get", timeout)
    monkeypatch.setattr(cert_expiry, "_tls_lookup", _fallback_result)

    result = get_cert_expiry("example.com")

    assert result.source == "tls"


class _FakeSocket:
    """A context-managed socket test double."""

    def __init__(self) -> None:
        self.timeout: float | None = None

    def __enter__(self) -> _FakeSocket:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def settimeout(self, timeout: float) -> None:
        self.timeout = timeout


class _FakeTlsSocket(_FakeSocket):
    """A TLS socket test double that supplies a peer certificate."""

    def __init__(self, certificate: dict[str, object]) -> None:
        super().__init__()
        self.certificate = certificate

    def getpeercert(self) -> dict[str, object]:
        return self.certificate


class _FakeTlsContext:
    """An SSL context test double that records the SNI hostname."""

    def __init__(self, certificate: dict[str, object]) -> None:
        self.certificate = certificate
        self.server_hostname: str | None = None
        self.connection_address: tuple[str, int] | None = None
        self.connection_timeout: float | None = None
        self.raw_socket = _FakeSocket()

    def wrap_socket(
        self, _: _FakeSocket, *, server_hostname: str
    ) -> _FakeTlsSocket:
        self.server_hostname = server_hostname
        return _FakeTlsSocket(self.certificate)


def _install_fake_tls(
    monkeypatch: pytest.MonkeyPatch, certificate: dict[str, object]
) -> _FakeTlsContext:
    """Install socket and SSL test doubles and return the recording context."""
    context = _FakeTlsContext(certificate)

    def create_connection(
        address: tuple[str, int], timeout: float
    ) -> _FakeSocket:
        context.connection_address = address
        context.connection_timeout = timeout
        return context.raw_socket

    monkeypatch.setattr(cert_expiry.ssl, "create_default_context", lambda: context)
    monkeypatch.setattr(cert_expiry.socket, "create_connection", create_connection)
    return context


def test_successful_direct_tls_fallback_uses_sni(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fallback retrieves and parses a peer certificate using SNI."""
    _mock_crt_payload(monkeypatch, [])
    certificate = {
        "notAfter": "Jan 15 23:59:59 2097 GMT",
        "issuer": (
            (("countryName", "US"),),
            (("organizationName", "Example CA"),),
            (("commonName", "Example Issuer"),),
        ),
    }
    context = _install_fake_tls(monkeypatch, certificate)

    result = get_cert_expiry("example.com")

    assert result.not_after == datetime(2097, 1, 15, 23, 59, 59, tzinfo=UTC)
    assert result.issuer == "Example CA, Example Issuer"
    assert result.source == "tls"
    assert context.server_hostname == "example.com"
    assert context.connection_address == ("example.com", 443)
    assert context.connection_timeout == cert_expiry.TLS_TIMEOUT_SECONDS
    assert context.raw_socket.timeout == cert_expiry.TLS_TIMEOUT_SECONDS


@pytest.mark.parametrize(
    ("failure", "expected_kind"),
    [
        (TimeoutError("timed out"), CertLookupErrorKind.TIMEOUT),
        (OSError("connection refused"), CertLookupErrorKind.UNREACHABLE),
    ],
)
def test_tls_connection_failure_is_classified(
    monkeypatch: pytest.MonkeyPatch,
    failure: OSError,
    expected_kind: CertLookupErrorKind,
) -> None:
    """TLS timeout and connection failures retain distinct classifications."""
    _mock_crt_payload(monkeypatch, [])

    def fail_connection(*_: object, **__: object) -> _FakeSocket:
        raise failure

    monkeypatch.setattr(cert_expiry.socket, "create_connection", fail_connection)

    with pytest.raises(CertLookupError) as raised:
        get_cert_expiry("example.com")

    assert raised.value.kind is expected_kind


def test_malformed_tls_certificate_is_classified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Malformed peer certificate metadata becomes a safe lookup error."""
    _mock_crt_payload(monkeypatch, [])
    _install_fake_tls(
        monkeypatch,
        {
            "notAfter": "not-a-certificate-time",
            "issuer": ((("commonName", "Example Issuer"),),),
        },
    )

    with pytest.raises(CertLookupError) as raised:
        get_cert_expiry("example.com")

    assert raised.value.kind is CertLookupErrorKind.MALFORMED_RESPONSE


def test_missing_tls_certificate_is_classified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A peer without certificate metadata becomes no_certificate."""
    _mock_crt_payload(monkeypatch, [])
    _install_fake_tls(monkeypatch, {})

    with pytest.raises(CertLookupError) as raised:
        get_cert_expiry("example.com")

    assert raised.value.kind is CertLookupErrorKind.NO_CERTIFICATE


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
def test_invalid_domain_is_rejected_before_network(
    monkeypatch: pytest.MonkeyPatch, invalid_domain: str
) -> None:
    """Malformed hostnames never trigger crt.sh or TLS network access."""

    def unexpected_http(*_: object, **__: object) -> httpx.Response:
        pytest.fail("Invalid domain input must not call crt.sh")

    def unexpected_tls(*_: object, **__: object) -> Any:
        pytest.fail("Invalid domain input must not open a TLS socket")

    monkeypatch.setattr(httpx, "get", unexpected_http)
    monkeypatch.setattr(cert_expiry.socket, "create_connection", unexpected_tls)

    with pytest.raises(CertLookupError) as raised:
        get_cert_expiry(invalid_domain)

    assert raised.value.kind is CertLookupErrorKind.INVALID_DOMAIN
