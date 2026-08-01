"""Passive dangling-CNAME and subdomain-takeover risk detection."""

from __future__ import annotations

import ipaddress
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, Pattern

import dns.exception
import dns.resolver
import httpx

from backend.detection.domain_expiry import DomainLookupError, normalize_domain

logger = logging.getLogger(__name__)

MAX_CNAME_HOPS = 10
DNS_TIMEOUT_SECONDS = 2.0
DNS_LIFETIME_SECONDS = 5.0
CONFIRMATION_MAX_BYTES = 256 * 1024
CONFIRMATION_TIMEOUT = httpx.Timeout(
    connect=3.0, read=5.0, write=3.0, pool=3.0
)
HTTP_HEADERS = {
    "Accept": "text/html, text/plain;q=0.9, */*;q=0.1",
    "User-Agent": "Aegis-Takeover-Risk/0.1",
}

# Canonical source: https://github.com/EdOverflow/can-i-take-over-xyz
# Verified as the current master commit on 2026-08-01. The pinned upstream
# fingerprint refresh was committed on 2025-02-08.
FINGERPRINT_COMMIT = "5bd4e12837911c8475486f1da922c9b9c706e632"
FINGERPRINT_REFRESH_DATE = "2025-02-08"
FINGERPRINT_URL = (
    "https://raw.githubusercontent.com/EdOverflow/can-i-take-over-xyz/"
    f"{FINGERPRINT_COMMIT}/fingerprints.json"
)


class TakeoverLookupErrorKind(StrEnum):
    """Stable categories for safe takeover-risk lookup failures."""

    INVALID_DOMAIN = "invalid_domain"
    DNS_TIMEOUT = "dns_timeout"
    DNS_UNREACHABLE = "dns_unreachable"
    CNAME_LOOP = "cname_loop"
    FINGERPRINT_DATA_ERROR = "fingerprint_data_error"
    CONFIRMATION_UNREACHABLE = "confirmation_unreachable"


class TakeoverLookupError(RuntimeError):
    """A classified takeover-risk failure safe for future batch scans."""

    def __init__(
        self, kind: TakeoverLookupErrorKind, domain: str, message: str
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.domain = domain


@dataclass(frozen=True, slots=True)
class TakeoverRiskResult:
    """A CNAME risk result whose confidence states confirmation strength.

    ``has_dangling_cname`` means a CNAME matched a vulnerable upstream provider
    pattern. It does not imply compromise; only ``confidence="high"`` has a
    live unclaimed-resource confirmation.
    """

    domain: str
    has_dangling_cname: bool
    cname_target: str | None
    matched_service: str | None
    confidence: Literal["none", "pattern_only", "high"]


@dataclass(frozen=True, slots=True)
class _CnameResolution:
    """The normalized targets observed while following one CNAME chain."""

    targets: tuple[str, ...]
    terminal_nxdomain: bool

    @property
    def final_target(self) -> str | None:
        """Return the last target in the chain, if the input had a CNAME."""
        return self.targets[-1] if self.targets else None


@dataclass(frozen=True, slots=True)
class _Fingerprint:
    """A usable vulnerable-provider fingerprint from the pinned source."""

    service: str
    suffixes: tuple[str, ...]
    nxdomain: bool
    http_status: int | None
    pattern: Pattern[str] | None


@dataclass(frozen=True, slots=True)
class _HttpObservation:
    """Bounded response data from one confirmation request."""

    status_code: int
    body: str
    truncated: bool


class _Confirmation(StrEnum):
    """Internal outcome of the one bounded confirmation step."""

    CONFIRMED = "confirmed"
    LEGITIMATE = "legitimate"
    INCONCLUSIVE = "inconclusive"


def _normalize_input(domain: str) -> str:
    """Normalize input while preserving takeover-specific classification."""
    try:
        return normalize_domain(domain)
    except DomainLookupError as exc:
        raise TakeoverLookupError(
            TakeoverLookupErrorKind.INVALID_DOMAIN, str(domain), str(exc)
        ) from exc


def _lookup_error(
    kind: TakeoverLookupErrorKind, domain: str, message: str
) -> TakeoverLookupError:
    """Log and create a classified external lookup error."""
    logger.warning("Takeover lookup failed for %s: %s", domain, message)
    return TakeoverLookupError(kind, domain, message)


def _build_resolver(domain: str) -> dns.resolver.Resolver:
    """Create a resolver with explicit per-query and total timeouts."""
    try:
        resolver = dns.resolver.Resolver(configure=True)
    except dns.resolver.NoResolverConfiguration as exc:
        raise _lookup_error(
            TakeoverLookupErrorKind.DNS_UNREACHABLE,
            domain,
            "No DNS resolver is configured",
        ) from exc
    resolver.timeout = DNS_TIMEOUT_SECONDS
    resolver.lifetime = DNS_LIFETIME_SECONDS
    return resolver


def _resolve_cname_chain(
    domain: str, resolver: dns.resolver.Resolver
) -> _CnameResolution:
    """Resolve one bounded CNAME chain without discovering other hostnames."""
    current = domain
    visited = {domain}
    targets: list[str] = []
    for _ in range(MAX_CNAME_HOPS):
        try:
            answer = resolver.resolve(
                current, "CNAME", lifetime=DNS_LIFETIME_SECONDS, search=False
            )
        except dns.resolver.NoAnswer:
            return _CnameResolution(tuple(targets), terminal_nxdomain=False)
        except dns.resolver.NXDOMAIN:
            return _CnameResolution(
                tuple(targets), terminal_nxdomain=bool(targets)
            )
        except (dns.resolver.LifetimeTimeout, dns.exception.Timeout) as exc:
            raise _lookup_error(
                TakeoverLookupErrorKind.DNS_TIMEOUT,
                domain,
                f"DNS timed out while resolving {current}",
            ) from exc
        except dns.resolver.NoNameservers as exc:
            raise _lookup_error(
                TakeoverLookupErrorKind.DNS_UNREACHABLE,
                domain,
                f"No nameserver could answer for {current}",
            ) from exc
        except dns.exception.DNSException as exc:
            raise _lookup_error(
                TakeoverLookupErrorKind.DNS_UNREACHABLE,
                domain,
                f"DNS resolution failed for {current}",
            ) from exc
        target = _answer_target(answer, domain)
        if target in visited:
            raise _lookup_error(
                TakeoverLookupErrorKind.CNAME_LOOP,
                domain,
                f"CNAME loop detected at {target}",
            )
        targets.append(target)
        visited.add(target)
        current = target
    raise _lookup_error(
        TakeoverLookupErrorKind.DNS_UNREACHABLE,
        domain,
        f"CNAME chain exceeded {MAX_CNAME_HOPS} hops",
    )


def _answer_target(answer: object, domain: str) -> str:
    """Read and normalize the single target from a CNAME answer."""
    try:
        record = answer[0]  # type: ignore[index]
        raw_target = str(record.target)  # type: ignore[attr-defined]
        return normalize_domain(raw_target)
    except (AttributeError, IndexError, TypeError, DomainLookupError) as exc:
        raise _lookup_error(
            TakeoverLookupErrorKind.DNS_UNREACHABLE,
            domain,
            "DNS returned a malformed CNAME answer",
        ) from exc


def _fingerprint_error(domain: str, message: str) -> TakeoverLookupError:
    """Create a classified pinned-fingerprint data failure."""
    return _lookup_error(
        TakeoverLookupErrorKind.FINGERPRINT_DATA_ERROR, domain, message
    )


def _load_fingerprints(domain: str) -> tuple[_Fingerprint, ...]:
    """Fetch and parse vulnerable entries from the immutable upstream source."""
    try:
        response = httpx.get(
            FINGERPRINT_URL,
            headers=HTTP_HEADERS,
            timeout=CONFIRMATION_TIMEOUT,
            follow_redirects=False,
        )
        response.raise_for_status()
    except httpx.TimeoutException as exc:
        raise _fingerprint_error(domain, "Fingerprint source timed out") from exc
    except (httpx.TransportError, httpx.HTTPStatusError) as exc:
        raise _fingerprint_error(
            domain, "Fingerprint source could not be reached"
        ) from exc
    try:
        payload = response.json()
    except ValueError as exc:
        raise _fingerprint_error(
            domain, "Fingerprint source returned invalid JSON"
        ) from exc
    return _parse_fingerprint_payload(payload, domain)


def _parse_fingerprint_payload(
    payload: object, domain: str
) -> tuple[_Fingerprint, ...]:
    """Parse only well-formed entries explicitly marked vulnerable upstream."""
    if not isinstance(payload, list):
        raise _fingerprint_error(domain, "Fingerprint data must be a JSON list")
    parsed = [
        fingerprint
        for entry in payload
        if (fingerprint := _parse_fingerprint_entry(entry)) is not None
    ]
    return tuple(parsed)


def _parse_fingerprint_entry(entry: object) -> _Fingerprint | None:
    """Return one usable vulnerable fingerprint; ignore all other entries."""
    if not isinstance(entry, Mapping) or entry.get("vulnerable") is not True:
        return None
    service = entry.get("service")
    raw_suffixes = entry.get("cname")
    if not isinstance(service, str) or not service.strip():
        return None
    if not isinstance(raw_suffixes, list):
        return None
    suffixes = tuple(
        suffix
        for raw_suffix in raw_suffixes
        if isinstance(raw_suffix, str)
        and (suffix := _normalize_suffix(raw_suffix)) is not None
    )
    if not suffixes:
        return None
    nxdomain = entry.get("nxdomain") is True
    http_status = _http_status(entry.get("http_status"))
    pattern = _fingerprint_pattern(entry.get("fingerprint"), nxdomain, http_status)
    if not nxdomain and http_status is None and pattern is None:
        return None
    return _Fingerprint(service.strip(), suffixes, nxdomain, http_status, pattern)


def _normalize_suffix(value: str) -> str | None:
    """Normalize a provider suffix, ignoring malformed or address entries."""
    try:
        return normalize_domain(value)
    except DomainLookupError:
        return None


def _http_status(value: object) -> int | None:
    """Return a valid upstream HTTP status fingerprint, if present."""
    if isinstance(value, int) and not isinstance(value, bool) and 100 <= value <= 599:
        return value
    return None


def _fingerprint_pattern(
    value: object, nxdomain: bool, http_status: int | None
) -> Pattern[str] | None:
    """Compile one upstream HTTP regex, ignoring malformed expressions."""
    if nxdomain or http_status is not None:
        return None
    if not isinstance(value, str) or not value:
        return None
    try:
        return re.compile(value, flags=re.IGNORECASE)
    except re.error:
        return None


def _matches_suffix(target: str, suffix: str) -> bool:
    """Match a DNS suffix only at a label boundary."""
    return target == suffix or target.endswith(f".{suffix}")


def _find_fingerprint(
    target: str, fingerprints: tuple[_Fingerprint, ...]
) -> _Fingerprint | None:
    """Return the most-specific vulnerable provider match for a target."""
    matches = [
        (len(suffix), fingerprint)
        for fingerprint in fingerprints
        for suffix in fingerprint.suffixes
        if _matches_suffix(target, suffix)
    ]
    return max(matches, key=lambda match: match[0])[1] if matches else None


def _confirmation_error(domain: str, message: str) -> TakeoverLookupError:
    """Create a classified but conservatively recoverable confirmation error."""
    return _lookup_error(
        TakeoverLookupErrorKind.CONFIRMATION_UNREACHABLE, domain, message
    )


def _public_target_addresses(
    target: str, domain: str, resolver: dns.resolver.Resolver
) -> tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...]:
    """Resolve and validate every terminal target address before HTTP."""
    addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for record_type in ("A", "AAAA"):
        try:
            answer = resolver.resolve(
                target, record_type, lifetime=DNS_LIFETIME_SECONDS, search=False
            )
        except dns.resolver.NoAnswer:
            continue
        except dns.resolver.NXDOMAIN as exc:
            raise _confirmation_error(
                domain, "Confirmation target does not resolve"
            ) from exc
        except (dns.resolver.LifetimeTimeout, dns.exception.Timeout) as exc:
            raise _confirmation_error(
                domain, "Confirmation address lookup timed out"
            ) from exc
        except (dns.resolver.NoNameservers, dns.exception.DNSException) as exc:
            raise _confirmation_error(
                domain, "Confirmation address lookup failed"
            ) from exc
        addresses.extend(_answer_addresses(answer, domain))
    if not addresses:
        raise _confirmation_error(domain, "Confirmation target has no IP address")
    if any(not address.is_global for address in addresses):
        raise _confirmation_error(
            domain, "Confirmation target resolved to a non-public address"
        )
    return tuple(addresses)


def _answer_addresses(
    answer: object, domain: str
) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """Parse IP addresses from one DNS answer."""
    addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    try:
        records = list(answer)  # type: ignore[arg-type]
    except TypeError as exc:
        raise _confirmation_error(domain, "Malformed address response") from exc
    for record in records:
        raw_address = getattr(record, "address", str(record))
        try:
            addresses.append(ipaddress.ip_address(raw_address))
        except ValueError as exc:
            raise _confirmation_error(domain, "Malformed target address") from exc
    return addresses


def _read_bounded_body(response: httpx.Response) -> tuple[str, bool]:
    """Read at most the configured number of confirmation response bytes."""
    body = bytearray()
    truncated = False
    for chunk in response.iter_bytes(chunk_size=16 * 1024):
        remaining = CONFIRMATION_MAX_BYTES - len(body)
        if len(chunk) > remaining:
            body.extend(chunk[:remaining])
            truncated = True
            break
        body.extend(chunk)
        if len(body) == CONFIRMATION_MAX_BYTES:
            truncated = True
            break
    encoding = response.encoding or "utf-8"
    try:
        decoded = body.decode(encoding, errors="replace")
    except LookupError:
        decoded = body.decode("utf-8", errors="replace")
    return decoded, truncated


def _http_observation(
    domain: str, target: str, resolver: dns.resolver.Resolver
) -> _HttpObservation:
    """Make one bounded HTTPS request after rejecting non-public targets."""
    _public_target_addresses(target, domain, resolver)
    try:
        with httpx.stream(
            "GET",
            f"https://{domain}/",
            headers=HTTP_HEADERS,
            timeout=CONFIRMATION_TIMEOUT,
            follow_redirects=False,
        ) as response:
            body, truncated = _read_bounded_body(response)
            return _HttpObservation(response.status_code, body, truncated)
    except httpx.TimeoutException as exc:
        raise _confirmation_error(domain, "Confirmation request timed out") from exc
    except httpx.TransportError as exc:
        raise _confirmation_error(
            domain, "Confirmation endpoint could not be reached"
        ) from exc


def _confirm_fingerprint(
    domain: str,
    target: str,
    resolution: _CnameResolution,
    fingerprint: _Fingerprint,
    resolver: dns.resolver.Resolver,
) -> _Confirmation:
    """Confirm one provider pattern through exact NXDOMAIN or bounded HTTPS."""
    if fingerprint.nxdomain:
        return (
            _Confirmation.CONFIRMED
            if resolution.terminal_nxdomain
            else _Confirmation.INCONCLUSIVE
        )
    observation = _http_observation(domain, target, resolver)
    if fingerprint.http_status is not None:
        return (
            _Confirmation.CONFIRMED
            if observation.status_code == fingerprint.http_status
            else _Confirmation.LEGITIMATE
        )
    if fingerprint.pattern is not None and fingerprint.pattern.search(observation.body):
        return _Confirmation.CONFIRMED
    return (
        _Confirmation.INCONCLUSIVE
        if observation.truncated
        else _Confirmation.LEGITIMATE
    )


def _no_risk(domain: str, target: str | None = None) -> TakeoverRiskResult:
    """Build a result with no provider-confirmed dangling-CNAME risk."""
    return TakeoverRiskResult(domain, False, target, None, "none")


def check_takeover_risk(domain: str) -> TakeoverRiskResult:
    """Assess one exact hostname for a confirmed or possible dangling CNAME.

    Args:
        domain: The exact authorized bare hostname to inspect; no discovery or
            subdomain enumeration is performed.

    Returns:
        A typed result distinguishing no risk, a provider pattern, and a live
        high-confidence unclaimed-resource signature.

    Raises:
        TakeoverLookupError: If input, DNS, or pinned fingerprint data prevents
            a safe assessment. Confirmation failures degrade to pattern-only.
    """
    normalized_domain = _normalize_input(domain)
    resolver = _build_resolver(normalized_domain)
    resolution = _resolve_cname_chain(normalized_domain, resolver)
    target = resolution.final_target
    if target is None:
        return _no_risk(normalized_domain)
    fingerprint = _find_fingerprint(target, _load_fingerprints(normalized_domain))
    if fingerprint is None:
        return _no_risk(normalized_domain, target)
    try:
        confirmation = _confirm_fingerprint(
            normalized_domain, target, resolution, fingerprint, resolver
        )
    except TakeoverLookupError as exc:
        if exc.kind is not TakeoverLookupErrorKind.CONFIRMATION_UNREACHABLE:
            raise
        confirmation = _Confirmation.INCONCLUSIVE
    if confirmation is _Confirmation.LEGITIMATE:
        return TakeoverRiskResult(
            normalized_domain, False, target, fingerprint.service, "none"
        )
    confidence: Literal["pattern_only", "high"] = (
        "high" if confirmation is _Confirmation.CONFIRMED else "pattern_only"
    )
    return TakeoverRiskResult(
        normalized_domain, True, target, fingerprint.service, confidence
    )
