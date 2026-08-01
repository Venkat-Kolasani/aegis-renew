"""Offline tests for dangling-CNAME and takeover-risk detection."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import nullcontext
from dataclasses import dataclass

import dns.exception
import dns.resolver
import httpx
import pytest

from backend.detection import takeover_risk
from backend.detection.takeover_risk import (
    TakeoverLookupError,
    TakeoverLookupErrorKind,
    check_takeover_risk,
)


@pytest.fixture(autouse=True)
def _clear_fingerprint_payload_cache() -> Iterator[None]:
    """Keep fingerprint fetch caching isolated across offline tests."""
    takeover_risk._fetch_fingerprint_payload.cache_clear()
    yield
    takeover_risk._fetch_fingerprint_payload.cache_clear()


@dataclass(frozen=True)
class _CnameRecord:
    """Minimal CNAME record test double."""

    target: str


@dataclass(frozen=True)
class _AddressRecord:
    """Minimal address record test double."""

    address: str


class _FakeResolver:
    """A scripted resolver that never accesses live DNS."""

    def __init__(self, records: dict[tuple[str, str], object]) -> None:
        self.records = records
        self.calls: list[tuple[str, str]] = []

    def resolve(
        self, name: str, record_type: str, **_: object
    ) -> list[object]:
        self.calls.append((name, record_type))
        outcome = self.records.get((name, record_type), dns.resolver.NoAnswer())
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome  # type: ignore[return-value]


def _install_resolver(
    monkeypatch: pytest.MonkeyPatch,
    records: dict[tuple[str, str], object],
) -> _FakeResolver:
    """Install and return a scripted resolver."""
    resolver = _FakeResolver(records)
    monkeypatch.setattr(takeover_risk, "_build_resolver", lambda _: resolver)
    return resolver


def _fingerprint_payload(
    *,
    vulnerable: bool = True,
    suffix: str = "s3.amazonaws.com",
    service: str = "AWS/S3",
    fingerprint: str = "The specified bucket does not exist",
    nxdomain: bool = False,
    http_status: int | None = None,
) -> dict[str, object]:
    """Build one upstream-shaped fingerprint entry."""
    return {
        "cname": [suffix],
        "fingerprint": fingerprint,
        "http_status": http_status,
        "nxdomain": nxdomain,
        "service": service,
        "vulnerable": vulnerable,
    }


def _install_fingerprints(
    monkeypatch: pytest.MonkeyPatch, *payloads: dict[str, object]
) -> None:
    """Install parsed pinned-source entries without network access."""
    parsed = takeover_risk._parse_fingerprint_payload(
        list(payloads), "app.example.com"
    )
    monkeypatch.setattr(takeover_risk, "_load_fingerprints", lambda _: parsed)


def _cname_records(
    source: str, target: str
) -> dict[tuple[str, str], object]:
    """Build records for a one-hop CNAME with a terminal NoAnswer."""
    return {
        (source, "CNAME"): [_CnameRecord(f"{target}.")],
        (target, "CNAME"): dns.resolver.NoAnswer(),
    }


def test_no_cname_returns_no_risk(monkeypatch: pytest.MonkeyPatch) -> None:
    """A hostname without a CNAME has no dangling-CNAME signal."""
    _install_resolver(monkeypatch, {})

    result = check_takeover_risk("app.example.com")

    assert result.has_dangling_cname is False
    assert result.cname_target is None
    assert result.confidence == "none"


def test_multi_hop_cname_chain_returns_final_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The resolver follows each CNAME and returns the normalized terminal host."""
    _install_resolver(
        monkeypatch,
        {
            ("app.example.com", "CNAME"): [_CnameRecord("edge.provider.test.")],
            ("edge.provider.test", "CNAME"): [_CnameRecord("final.provider.test.")],
        },
    )
    _install_fingerprints(monkeypatch)

    result = check_takeover_risk("APP.EXAMPLE.COM.")

    assert result.domain == "app.example.com"
    assert result.cname_target == "final.provider.test"
    assert result.confidence == "none"


def test_cname_chain_over_maximum_is_classified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A chain beyond the configured hop boundary fails conservatively."""
    records: dict[tuple[str, str], object] = {}
    current = "app.example.com"
    for index in range(takeover_risk.MAX_CNAME_HOPS + 1):
        target = f"hop-{index}.provider.test"
        records[(current, "CNAME")] = [_CnameRecord(f"{target}.")]
        current = target
    _install_resolver(monkeypatch, records)

    with pytest.raises(TakeoverLookupError) as raised:
        check_takeover_risk("app.example.com")

    assert raised.value.kind is TakeoverLookupErrorKind.DNS_UNREACHABLE


def test_cname_loop_is_classified(monkeypatch: pytest.MonkeyPatch) -> None:
    """A repeated CNAME target is rejected as a loop."""
    _install_resolver(
        monkeypatch,
        {
            ("app.example.com", "CNAME"): [_CnameRecord("loop.example.com.")],
            ("loop.example.com", "CNAME"): [_CnameRecord("app.example.com.")],
        },
    )

    with pytest.raises(TakeoverLookupError) as raised:
        check_takeover_risk("app.example.com")

    assert raised.value.kind is TakeoverLookupErrorKind.CNAME_LOOP


def test_cname_target_with_underscore_is_normalized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolved DNS service labels may contain underscores."""
    target = "_service.s3.amazonaws.com"
    _install_resolver(
        monkeypatch,
        {
            ("app.example.com", "CNAME"): [
                _CnameRecord("_SERVICE.S3.AMAZONAWS.COM.")
            ],
            (target, "CNAME"): dns.resolver.NoAnswer(),
        },
    )
    _install_fingerprints(monkeypatch, _fingerprint_payload(nxdomain=True))

    result = check_takeover_risk("app.example.com")

    assert result.cname_target == target
    assert result.confidence == "pattern_only"


def test_malformed_cname_target_is_classified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Malformed resolver-provided names fail as DNS errors, not user input."""
    _install_resolver(
        monkeypatch,
        {
            ("app.example.com", "CNAME"): [
                _CnameRecord("bad..provider.test.")
            ]
        },
    )

    with pytest.raises(TakeoverLookupError) as raised:
        check_takeover_risk("app.example.com")

    assert raised.value.kind is TakeoverLookupErrorKind.DNS_UNREACHABLE


def test_vulnerable_suffix_match_is_pattern_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A vulnerable provider suffix without confirmation remains uncertain."""
    target = "missing.elasticbeanstalk.com"
    _install_resolver(
        monkeypatch, _cname_records("app.example.com", target)
    )
    _install_fingerprints(
        monkeypatch,
        _fingerprint_payload(
            suffix="elasticbeanstalk.com",
            service="AWS/Elastic Beanstalk",
            fingerprint="NXDOMAIN",
            nxdomain=True,
        ),
    )

    result = check_takeover_risk("app.example.com")

    assert result.has_dangling_cname is True
    assert result.matched_service == "AWS/Elastic Beanstalk"
    assert result.confidence == "pattern_only"


def test_suffix_boundary_prevents_false_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lookalike suffix without a DNS-label boundary never matches."""
    target = "evilgithub.io"
    _install_resolver(
        monkeypatch, _cname_records("app.example.com", target)
    )
    _install_fingerprints(
        monkeypatch,
        _fingerprint_payload(
            suffix="github.io",
            service="GitHub",
            fingerprint="NXDOMAIN",
            nxdomain=True,
        ),
    )

    result = check_takeover_risk("app.example.com")

    assert result.has_dangling_cname is False
    assert result.matched_service is None
    assert result.confidence == "none"


def test_http_fingerprint_confirms_high_confidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A live upstream HTTP signature confirms high confidence."""
    target = "missing.s3.amazonaws.com"
    records = _cname_records("app.example.com", target)
    records[(target, "A")] = [_AddressRecord("93.184.216.34")]
    records[("app.example.com", "A")] = [_AddressRecord("93.184.216.34")]
    _install_resolver(monkeypatch, records)
    _install_fingerprints(monkeypatch, _fingerprint_payload())
    response = httpx.Response(
        404,
        text="The specified bucket does not exist",
        request=httpx.Request("GET", "https://app.example.com/"),
    )
    requests: list[tuple[str, str, bool]] = []

    def stream(
        method: str, url: str, **kwargs: object
    ) -> nullcontext[httpx.Response]:
        requests.append((method, url, bool(kwargs.get("follow_redirects"))))
        return nullcontext(response)

    monkeypatch.setattr(httpx, "stream", stream)

    result = check_takeover_risk("app.example.com")

    assert result.has_dangling_cname is True
    assert result.confidence == "high"
    assert requests == [("GET", "https://app.example.com/", False)]


def test_nxdomain_fingerprint_confirms_exact_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NXDOMAIN on the exact terminal provider target confirms the signature."""
    target = "missing.elasticbeanstalk.com"
    _install_resolver(
        monkeypatch,
        {
            ("app.example.com", "CNAME"): [_CnameRecord(f"{target}.")],
            (target, "CNAME"): dns.resolver.NXDOMAIN(),
        },
    )
    _install_fingerprints(
        monkeypatch,
        _fingerprint_payload(
            suffix="elasticbeanstalk.com",
            service="AWS/Elastic Beanstalk",
            fingerprint="NXDOMAIN",
            nxdomain=True,
        ),
    )

    result = check_takeover_risk("app.example.com")

    assert result.cname_target == target
    assert result.confidence == "high"


def test_inconclusive_http_failure_stays_pattern_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A blocked confirmation request cannot elevate a provider pattern."""
    target = "missing.s3.amazonaws.com"
    _install_resolver(
        monkeypatch, _cname_records("app.example.com", target)
    )
    _install_fingerprints(monkeypatch, _fingerprint_payload())

    def fail_confirmation(*_: object) -> takeover_risk._HttpObservation:
        raise TakeoverLookupError(
            TakeoverLookupErrorKind.CONFIRMATION_UNREACHABLE,
            "app.example.com",
            "blocked",
        )

    monkeypatch.setattr(takeover_risk, "_http_observation", fail_confirmation)

    result = check_takeover_risk("app.example.com")

    assert result.has_dangling_cname is True
    assert result.confidence == "pattern_only"


def test_unexpected_server_error_stays_pattern_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unexpected provider 5xx response is never proof of legitimacy."""
    target = "missing.s3.amazonaws.com"
    _install_resolver(
        monkeypatch, _cname_records("app.example.com", target)
    )
    _install_fingerprints(monkeypatch, _fingerprint_payload())
    monkeypatch.setattr(
        takeover_risk,
        "_http_observation",
        lambda *_: takeover_risk._HttpObservation(
            503, "The specified bucket does not exist", False
        ),
    )

    result = check_takeover_risk("app.example.com")

    assert result.has_dangling_cname is True
    assert result.confidence == "pattern_only"


def test_explicit_server_status_fingerprint_can_confirm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicitly expected 5xx status remains a valid fingerprint."""
    target = "missing.s3.amazonaws.com"
    _install_resolver(
        monkeypatch, _cname_records("app.example.com", target)
    )
    _install_fingerprints(
        monkeypatch, _fingerprint_payload(fingerprint="", http_status=503)
    )
    monkeypatch.setattr(
        takeover_risk,
        "_http_observation",
        lambda *_: takeover_risk._HttpObservation(503, "Unavailable", False),
    )

    result = check_takeover_risk("app.example.com")

    assert result.confidence == "high"


def test_oversized_http_body_stays_pattern_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A truncated nonmatching provider response remains inconclusive."""
    target = "missing.s3.amazonaws.com"
    records = _cname_records("app.example.com", target)
    records[(target, "A")] = [_AddressRecord("93.184.216.34")]
    records[("app.example.com", "A")] = [_AddressRecord("93.184.216.34")]
    _install_resolver(monkeypatch, records)
    _install_fingerprints(monkeypatch, _fingerprint_payload())
    response = httpx.Response(
        200,
        content=b"x" * (takeover_risk.CONFIRMATION_MAX_BYTES + 1),
        request=httpx.Request("GET", "https://app.example.com/"),
    )
    monkeypatch.setattr(
        httpx, "stream", lambda *args, **kwargs: nullcontext(response)
    )

    result = check_takeover_risk("app.example.com")

    assert result.has_dangling_cname is True
    assert result.confidence == "pattern_only"


def test_legitimate_live_resource_clears_pattern_risk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A normal live response is not presented as a dangling resource."""
    target = "active.s3.amazonaws.com"
    _install_resolver(
        monkeypatch, _cname_records("app.example.com", target)
    )
    _install_fingerprints(monkeypatch, _fingerprint_payload())
    monkeypatch.setattr(
        takeover_risk,
        "_http_observation",
        lambda *_: takeover_risk._HttpObservation(200, "Active bucket", False),
    )

    result = check_takeover_risk("app.example.com")

    assert result.has_dangling_cname is False
    assert result.matched_service == "AWS/S3"
    assert result.confidence == "none"


def test_non_vulnerable_fingerprint_is_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An upstream vulnerable=false entry cannot produce a match."""
    target = "tenant.github.io"
    _install_resolver(
        monkeypatch, _cname_records("app.example.com", target)
    )
    _install_fingerprints(
        monkeypatch,
        _fingerprint_payload(
            vulnerable=False,
            suffix="github.io",
            service="GitHub",
        ),
    )

    result = check_takeover_risk("app.example.com")

    assert result.has_dangling_cname is False
    assert result.confidence == "none"


@pytest.mark.parametrize(
    ("failure", "kind"),
    [
        (dns.exception.Timeout(), TakeoverLookupErrorKind.DNS_TIMEOUT),
        (dns.resolver.NoNameservers(), TakeoverLookupErrorKind.DNS_UNREACHABLE),
    ],
)
def test_dns_failure_is_classified(
    monkeypatch: pytest.MonkeyPatch,
    failure: dns.exception.DNSException,
    kind: TakeoverLookupErrorKind,
) -> None:
    """Resolver timeouts and unavailable nameservers retain distinct errors."""
    _install_resolver(
        monkeypatch, {("app.example.com", "CNAME"): failure}
    )

    with pytest.raises(TakeoverLookupError) as raised:
        check_takeover_risk("app.example.com")

    assert raised.value.kind is kind


@pytest.mark.parametrize("address", ["127.0.0.1", "192.0.2.10"])
def test_non_public_target_blocks_http_confirmation(
    monkeypatch: pytest.MonkeyPatch, address: str
) -> None:
    """Private and reserved target addresses block the confirmation request."""
    target = "missing.s3.amazonaws.com"
    records = _cname_records("app.example.com", target)
    records[(target, "A")] = [_AddressRecord(address)]
    _install_resolver(monkeypatch, records)
    _install_fingerprints(monkeypatch, _fingerprint_payload())

    def unexpected_stream(*_: object, **__: object) -> object:
        pytest.fail("Non-public targets must not receive HTTP requests")

    monkeypatch.setattr(httpx, "stream", unexpected_stream)

    result = check_takeover_risk("app.example.com")

    assert result.has_dangling_cname is True
    assert result.confidence == "pattern_only"


def test_private_requested_hostname_blocks_http_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A public CNAME target cannot bypass a private requested hostname."""
    target = "missing.s3.amazonaws.com"
    records = _cname_records("app.example.com", target)
    records[(target, "A")] = [_AddressRecord("93.184.216.34")]
    records[("app.example.com", "A")] = [_AddressRecord("127.0.0.1")]
    _install_resolver(monkeypatch, records)
    _install_fingerprints(monkeypatch, _fingerprint_payload())

    def unexpected_stream(*_: object, **__: object) -> object:
        pytest.fail("Private requested hostnames must not receive HTTP requests")

    monkeypatch.setattr(httpx, "stream", unexpected_stream)

    result = check_takeover_risk("app.example.com")

    assert result.has_dangling_cname is True
    assert result.confidence == "pattern_only"


def test_plain_fingerprint_uses_case_insensitive_literal_semantics() -> None:
    """Plain punctuation is retained in a literal matcher, not compiled."""
    fingerprint = takeover_risk._parse_fingerprint_entry(
        _fingerprint_payload(fingerprint="Bucket unavailable: code=404!")
    )

    assert fingerprint is not None
    assert fingerprint.literal == "bucket unavailable: code=404!"
    assert fingerprint.pattern is None


def test_genuine_regex_fingerprint_remains_supported() -> None:
    """An upstream expression with regex metacharacters remains a regex."""
    fingerprint = takeover_risk._parse_fingerprint_entry(
        _fingerprint_payload(fingerprint=r"Bucket [0-9]+ unavailable")
    )

    assert fingerprint is not None
    assert fingerprint.literal is None
    assert fingerprint.pattern is not None
    assert fingerprint.pattern.search("bucket 42 unavailable") is not None


def test_invalid_regex_fingerprint_is_rejected() -> None:
    """An invalid upstream regex cannot become a usable fingerprint."""
    fingerprint = takeover_risk._parse_fingerprint_entry(
        _fingerprint_payload(fingerprint="[")
    )

    assert fingerprint is None


def test_successful_fingerprint_payload_fetch_is_reused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeated checks reuse the same successful pinned payload fetch."""
    target = "missing.elasticbeanstalk.com"
    _install_resolver(
        monkeypatch,
        {
            ("app.example.com", "CNAME"): [_CnameRecord(f"{target}.")],
            (target, "CNAME"): dns.resolver.NXDOMAIN(),
        },
    )
    response = httpx.Response(
        200,
        json=[
            _fingerprint_payload(
                suffix="elasticbeanstalk.com",
                service="AWS/Elastic Beanstalk",
                fingerprint="NXDOMAIN",
                nxdomain=True,
            )
        ],
        request=httpx.Request("GET", takeover_risk.FINGERPRINT_URL),
    )
    fetch_count = 0

    def get(*_: object, **__: object) -> httpx.Response:
        nonlocal fetch_count
        fetch_count += 1
        return response

    monkeypatch.setattr(httpx, "get", get)

    first = check_takeover_risk("app.example.com")
    second = check_takeover_risk("app.example.com")

    assert first.confidence == second.confidence == "high"
    assert fetch_count == 1


def test_malformed_fingerprint_data_is_classified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A malformed root payload cannot silently disable provider matching."""
    target = "missing.s3.amazonaws.com"
    _install_resolver(
        monkeypatch, _cname_records("app.example.com", target)
    )
    response = httpx.Response(
        200,
        json={"unexpected": "object"},
        request=httpx.Request("GET", takeover_risk.FINGERPRINT_URL),
    )
    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: response)

    with pytest.raises(TakeoverLookupError) as raised:
        check_takeover_risk("app.example.com")

    assert raised.value.kind is TakeoverLookupErrorKind.FINGERPRINT_DATA_ERROR


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
    ],
)
def test_invalid_hostname_is_rejected_before_dns(
    monkeypatch: pytest.MonkeyPatch, invalid_domain: str
) -> None:
    """Invalid exact hostnames never trigger DNS or HTTP activity."""

    def unexpected_resolver(_: str) -> dns.resolver.Resolver:
        pytest.fail("Invalid input must not create a resolver")

    monkeypatch.setattr(takeover_risk, "_build_resolver", unexpected_resolver)

    with pytest.raises(TakeoverLookupError) as raised:
        check_takeover_risk(invalid_domain)

    assert raised.value.kind is TakeoverLookupErrorKind.INVALID_DOMAIN
