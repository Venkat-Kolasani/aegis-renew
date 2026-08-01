"""TLS certificate-expiration detection using crt.sh with a live fallback."""

from __future__ import annotations

import logging
import socket
import ssl
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

import httpx

from backend.detection.domain_expiry import DomainLookupError, normalize_domain

logger = logging.getLogger(__name__)

CRT_SH_URL = "https://crt.sh/"
CRT_SH_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0)
CRT_SH_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "Aegis-Certificate-Monitor/0.1",
}
TLS_PORT = 443
TLS_TIMEOUT_SECONDS = 8.0


class CertLookupErrorKind(StrEnum):
    """Stable categories callers can use to handle certificate failures."""

    INVALID_DOMAIN = "invalid_domain"
    TIMEOUT = "timeout"
    UNREACHABLE = "unreachable"
    MALFORMED_RESPONSE = "malformed_response"
    NO_CERTIFICATE = "no_certificate"


class CertLookupError(RuntimeError):
    """A classified certificate lookup failure safe for batch processing."""

    def __init__(
        self, kind: CertLookupErrorKind, domain: str, message: str
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.domain = domain


@dataclass(frozen=True, slots=True)
class CertExpiryResult:
    """Normalized certificate-expiration facts for one hostname."""

    domain: str
    not_after: datetime
    issuer: str
    source: Literal["crt.sh", "tls"]


@dataclass(frozen=True, slots=True)
class _CertificateCandidate:
    """A usable, hostname-matching certificate parsed from crt.sh."""

    not_before: datetime
    not_after: datetime
    issuer: str
    names: frozenset[str]
    identity: tuple[str, ...]


class _CrtShFallbackNeeded(RuntimeError):
    """Signals that crt.sh cannot provide a usable primary result."""


def _normalize_hostname(domain: str) -> str:
    """Apply the established RDAP hostname normalization behavior."""
    try:
        return normalize_domain(domain)
    except DomainLookupError as exc:
        raise CertLookupError(
            CertLookupErrorKind.INVALID_DOMAIN, str(domain), str(exc)
        ) from exc


def _parse_crt_datetime(value: object) -> datetime:
    """Parse a crt.sh timestamp and normalize it to timezone-aware UTC."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Certificate timestamp is missing")
    normalized = value.strip().replace(" ", "T")
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _normalize_certificate_name(name: str) -> str | None:
    """Normalize a crt.sh DNS name while preserving a leading wildcard."""
    candidate = name.strip().lower()
    wildcard = candidate.startswith("*.")
    if wildcard:
        candidate = candidate[2:]
    try:
        normalized = normalize_domain(candidate)
    except DomainLookupError:
        return None
    return f"*.{normalized}" if wildcard else normalized


def _certificate_names(row: Mapping[str, object]) -> frozenset[str]:
    """Collect normalized common-name and SAN-style crt.sh name values."""
    names: set[str] = set()
    for field in ("common_name", "name_value"):
        value = row.get(field)
        if not isinstance(value, str):
            continue
        for raw_name in value.splitlines():
            normalized = _normalize_certificate_name(raw_name)
            if normalized is not None:
                names.add(normalized)
    return frozenset(names)


def _covers_hostname(certificate_name: str, domain: str) -> bool:
    """Return whether an exact or single-label wildcard name covers a host."""
    if certificate_name == domain:
        return True
    if not certificate_name.startswith("*."):
        return False
    suffix = certificate_name[2:]
    return domain.endswith(f".{suffix}") and (
        domain.count(".") == suffix.count(".") + 1
    )


def _parse_crt_candidate(
    row: object, domain: str
) -> _CertificateCandidate | None:
    """Parse one crt.sh row, returning only a usable hostname match."""
    if not isinstance(row, Mapping):
        return None
    issuer = row.get("issuer_name")
    if not isinstance(issuer, str) or not issuer.strip():
        return None
    names = _certificate_names(row)
    if not any(_covers_hostname(name, domain) for name in names):
        return None
    try:
        not_before = _parse_crt_datetime(row.get("not_before"))
        not_after = _parse_crt_datetime(row.get("not_after"))
    except (OSError, OverflowError, TypeError, ValueError):
        return None
    if not_before > not_after:
        return None
    identity = _certificate_identity(row, issuer, not_before, not_after, names)
    return _CertificateCandidate(
        not_before=not_before,
        not_after=not_after,
        issuer=issuer.strip(),
        names=names,
        identity=identity,
    )


def _certificate_identity(
    row: Mapping[str, object],
    issuer: str,
    not_before: datetime,
    not_after: datetime,
    names: frozenset[str],
) -> tuple[str, ...]:
    """Build a stable identity that deduplicates one issuer's serial number."""
    serial = row.get("serial_number")
    if isinstance(serial, str) and serial.strip():
        return ("serial", issuer.casefold(), serial.strip().casefold())
    return (
        "fields",
        issuer.casefold(),
        not_before.isoformat(),
        not_after.isoformat(),
        *sorted(names),
    )


def _fetch_crt_candidates(domain: str) -> list[_CertificateCandidate]:
    """Fetch, filter, and deduplicate matching certificates from crt.sh."""
    try:
        response = httpx.get(
            CRT_SH_URL,
            params={"q": domain, "output": "json"},
            headers=CRT_SH_HEADERS,
            timeout=CRT_SH_TIMEOUT,
            follow_redirects=True,
        )
        response.raise_for_status()
    except httpx.TimeoutException as exc:
        raise _CrtShFallbackNeeded("crt.sh request timed out") from exc
    except (httpx.TransportError, httpx.HTTPStatusError) as exc:
        raise _CrtShFallbackNeeded("crt.sh could not be reached") from exc
    try:
        payload = response.json()
    except ValueError as exc:
        raise _CrtShFallbackNeeded("crt.sh returned invalid JSON") from exc
    if not isinstance(payload, list):
        raise _CrtShFallbackNeeded("crt.sh returned a non-list response")

    unique: dict[tuple[str, ...], _CertificateCandidate] = {}
    for row in payload:
        candidate = _parse_crt_candidate(row, domain)
        if candidate is not None:
            unique[candidate.identity] = candidate
    if not unique:
        raise _CrtShFallbackNeeded("crt.sh returned no usable matching certificate")
    return list(unique.values())


def _select_crt_candidate(
    candidates: list[_CertificateCandidate], now: datetime
) -> _CertificateCandidate:
    """Select the latest current certificate, or latest expired if all expired."""
    current = [
        candidate
        for candidate in candidates
        if candidate.not_before <= now <= candidate.not_after
    ]
    if current:
        return max(current, key=lambda candidate: candidate.not_after)
    expired = [candidate for candidate in candidates if candidate.not_after < now]
    if len(expired) == len(candidates):
        return max(expired, key=lambda candidate: candidate.not_after)
    raise _CrtShFallbackNeeded("crt.sh has no currently valid certificate")


def _crt_sh_lookup(domain: str) -> CertExpiryResult:
    """Return the best hostname-matching certificate published by crt.sh."""
    candidates = _fetch_crt_candidates(domain)
    selected = _select_crt_candidate(candidates, datetime.now(UTC))
    return CertExpiryResult(
        domain=domain,
        not_after=selected.not_after,
        issuer=selected.issuer,
        source="crt.sh",
    )


def _tls_error(
    kind: CertLookupErrorKind, domain: str, message: str
) -> CertLookupError:
    """Log and construct a classified direct-TLS lookup error."""
    logger.warning("Direct TLS lookup failed for %s: %s", domain, message)
    return CertLookupError(kind, domain, message)


def _tls_peer_certificate(domain: str) -> Mapping[str, object]:
    """Retrieve a verified peer certificate using a bounded SNI handshake."""
    try:
        context = ssl.create_default_context()
        with socket.create_connection(
            (domain, TLS_PORT), timeout=TLS_TIMEOUT_SECONDS
        ) as raw_socket:
            raw_socket.settimeout(TLS_TIMEOUT_SECONDS)
            with context.wrap_socket(
                raw_socket, server_hostname=domain
            ) as tls_socket:
                certificate = tls_socket.getpeercert()
    except TimeoutError as exc:
        raise _tls_error(
            CertLookupErrorKind.TIMEOUT, domain, "TLS handshake timed out"
        ) from exc
    except ssl.SSLError as exc:
        raise _tls_error(
            CertLookupErrorKind.UNREACHABLE, domain, "TLS handshake failed"
        ) from exc
    except OSError as exc:
        raise _tls_error(
            CertLookupErrorKind.UNREACHABLE, domain, "TLS endpoint is unreachable"
        ) from exc
    if not isinstance(certificate, Mapping) or not certificate:
        raise _tls_error(
            CertLookupErrorKind.NO_CERTIFICATE,
            domain,
            "TLS peer did not provide a certificate",
        )
    return certificate


def _tls_not_after(certificate: Mapping[str, object], domain: str) -> datetime:
    """Parse a peer certificate's OpenSSL expiration value as UTC."""
    value = certificate.get("notAfter")
    if not isinstance(value, str):
        raise _tls_error(
            CertLookupErrorKind.MALFORMED_RESPONSE,
            domain,
            "TLS certificate expiration is missing",
        )
    try:
        timestamp = ssl.cert_time_to_seconds(value)
        not_after = datetime.fromtimestamp(timestamp, tz=UTC)
    except (OSError, OverflowError, ValueError) as exc:
        raise _tls_error(
            CertLookupErrorKind.MALFORMED_RESPONSE,
            domain,
            "TLS certificate expiration is malformed",
        ) from exc
    return not_after


def _tls_issuer(certificate: Mapping[str, object], domain: str) -> str:
    """Flatten a peer certificate issuer into a stable display string."""
    issuer = certificate.get("issuer")
    if not isinstance(issuer, (list, tuple)):
        raise _tls_error(
            CertLookupErrorKind.MALFORMED_RESPONSE,
            domain,
            "TLS certificate issuer is missing",
        )
    preferred: list[str] = []
    fallback: list[str] = []
    for rdn in issuer:
        if not isinstance(rdn, (list, tuple)):
            continue
        for attribute in rdn:
            if not isinstance(attribute, (list, tuple)) or len(attribute) != 2:
                continue
            key, value = attribute
            if not isinstance(key, str) or not isinstance(value, str):
                continue
            fallback.append(value)
            if key in {"organizationName", "commonName"}:
                preferred.append(value)
    values = preferred or fallback
    unique_values = list(
        dict.fromkeys(value.strip() for value in values if value.strip())
    )
    if not unique_values:
        raise _tls_error(
            CertLookupErrorKind.MALFORMED_RESPONSE,
            domain,
            "TLS certificate issuer is malformed",
        )
    return ", ".join(unique_values)


def _tls_lookup(domain: str) -> CertExpiryResult:
    """Return certificate facts from the hostname's direct TLS endpoint."""
    certificate = _tls_peer_certificate(domain)
    return CertExpiryResult(
        domain=domain,
        not_after=_tls_not_after(certificate, domain),
        issuer=_tls_issuer(certificate, domain),
        source="tls",
    )


def get_cert_expiry(domain: str) -> CertExpiryResult:
    """Return certificate expiry facts from crt.sh or a direct TLS fallback.

    Args:
        domain: A bare hostname, optionally Unicode or ending in a root dot.

    Returns:
        A ``CertExpiryResult`` with a UTC expiration, issuer, and exact source.

    Raises:
        CertLookupError: If input is invalid or neither crt.sh nor direct TLS
            can provide a usable certificate result.
    """
    normalized_domain = _normalize_hostname(domain)
    try:
        return _crt_sh_lookup(normalized_domain)
    except _CrtShFallbackNeeded as exc:
        logger.warning(
            "crt.sh unavailable or unusable for %s; using TLS fallback: %s",
            normalized_domain,
            exc,
        )
        return _tls_lookup(normalized_domain)
