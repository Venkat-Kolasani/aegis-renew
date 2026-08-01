"""Domain-expiration lookup through IANA-bootstrapped RDAP services."""

from __future__ import annotations

import ipaddress
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from urllib.parse import quote, urlparse

import httpx

logger = logging.getLogger(__name__)

IANA_RDAP_BOOTSTRAP_URL = "https://data.iana.org/rdap/dns.json"
RDAP_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
RDAP_HEADERS = {
    "Accept": "application/rdap+json, application/json",
    "User-Agent": "Aegis-RDAP/0.1",
}
DOMAIN_LABEL_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
EXPIRATION_ACTIONS = frozenset({"expiration", "expiry"})


class DomainLookupErrorKind(StrEnum):
    """Stable categories callers can use to handle domain lookup failures."""

    INVALID_DOMAIN = "invalid_domain"
    NOT_FOUND = "not_found"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    MALFORMED_RESPONSE = "malformed_response"


class DomainLookupError(RuntimeError):
    """A classified domain lookup failure safe for batch processing."""

    def __init__(
        self, kind: DomainLookupErrorKind, domain: str, message: str
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.domain = domain


@dataclass(frozen=True, slots=True)
class DomainExpiryResult:
    """Normalized domain-expiration facts returned by an RDAP registry."""

    domain: str
    expiry_date: date
    registrar: str | None
    raw_status: tuple[str, ...]


def _invalid_domain(domain: object, message: str) -> DomainLookupError:
    """Build a consistently classified input-validation error."""
    return DomainLookupError(
        DomainLookupErrorKind.INVALID_DOMAIN, str(domain), message
    )


def normalize_domain(domain: str) -> str:
    """Validate and normalize a registrable domain to lowercase IDNA ASCII.

    Args:
        domain: A bare domain name, optionally Unicode or ending in a root dot.

    Returns:
        The normalized ASCII hostname suitable for external lookups.

    Raises:
        DomainLookupError: If the value is not a valid bare domain name.
    """
    if not isinstance(domain, str):
        raise _invalid_domain(domain, "Domain must be a string")

    candidate = domain.strip().lower()
    if candidate.endswith("."):
        candidate = candidate[:-1]
    if not candidate:
        raise _invalid_domain(domain, "Domain cannot be empty")
    if any(marker in candidate for marker in ("://", "/", "\\", ":", "*")):
        raise _invalid_domain(domain, "Expected a domain name without URL syntax")

    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        pass
    else:
        raise _invalid_domain(domain, "IP addresses are not domain names")

    try:
        ascii_domain = candidate.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise _invalid_domain(
            domain, "Domain contains invalid IDNA characters"
        ) from exc
    labels = ascii_domain.split(".")
    if len(labels) < 2 or len(ascii_domain) > 253:
        raise _invalid_domain(domain, "Domain must contain a registrable suffix")
    if any(not DOMAIN_LABEL_PATTERN.fullmatch(label) for label in labels):
        raise _invalid_domain(domain, "Domain contains a malformed label")
    return ascii_domain


def _provider_error(domain: str, message: str) -> DomainLookupError:
    """Log and build an unavailable-provider lookup error."""
    logger.warning("RDAP provider unavailable for %s: %s", domain, message)
    return DomainLookupError(
        DomainLookupErrorKind.PROVIDER_UNAVAILABLE, domain, message
    )


def _malformed_response(domain: str, message: str) -> DomainLookupError:
    """Log and build a malformed-RDAP-response error."""
    logger.warning("Malformed RDAP response for %s: %s", domain, message)
    return DomainLookupError(
        DomainLookupErrorKind.MALFORMED_RESPONSE, domain, message
    )


def _get_response(url: str, domain: str, *, domain_query: bool) -> httpx.Response:
    """Fetch one RDAP resource and translate transport and HTTP failures."""
    try:
        response = httpx.get(
            url, headers=RDAP_HEADERS, timeout=RDAP_TIMEOUT, follow_redirects=True
        )
    except httpx.TimeoutException as exc:
        raise _provider_error(domain, "RDAP request timed out") from exc
    except httpx.TransportError as exc:
        raise _provider_error(domain, "RDAP provider could not be reached") from exc

    if domain_query and response.status_code == httpx.codes.NOT_FOUND:
        logger.info("RDAP domain not found: %s", domain)
        raise DomainLookupError(
            DomainLookupErrorKind.NOT_FOUND, domain, "Domain was not found by RDAP"
        )
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise _provider_error(
            domain, f"RDAP provider returned HTTP {response.status_code}"
        ) from exc
    return response


def _response_json(response: httpx.Response, domain: str) -> Mapping[str, object]:
    """Decode an RDAP response as a JSON object."""
    try:
        payload = response.json()
    except ValueError as exc:
        raise _malformed_response(domain, "Response was not valid JSON") from exc
    if not isinstance(payload, Mapping):
        raise _malformed_response(domain, "Expected a JSON object")
    return payload


def _rdap_base_url(domain: str) -> str:
    """Resolve a domain's authoritative RDAP service through IANA bootstrap."""
    response = _get_response(IANA_RDAP_BOOTSTRAP_URL, domain, domain_query=False)
    services = _response_json(response, domain).get("services")
    if not isinstance(services, Sequence) or isinstance(services, (str, bytes)):
        raise _malformed_response(domain, "IANA bootstrap services are missing")

    tld = domain.rsplit(".", maxsplit=1)[-1]
    for service in services:
        base_url = _matching_service_url(service, tld)
        if base_url is not None:
            return base_url
    raise DomainLookupError(
        DomainLookupErrorKind.NOT_FOUND,
        domain,
        f"No RDAP service is registered for .{tld}",
    )


def _matching_service_url(service: object, tld: str) -> str | None:
    """Return the first secure service URL matching the requested TLD."""
    if not isinstance(service, Sequence) or isinstance(service, (str, bytes)):
        return None
    if len(service) != 2:
        return None
    suffixes, urls = service
    if not isinstance(suffixes, list) or tld not in suffixes:
        return None
    if not isinstance(urls, list):
        return None
    for url in urls:
        if isinstance(url, str) and _is_secure_base_url(url):
            return url
    return None


def _is_secure_base_url(url: str) -> bool:
    """Return whether a bootstrap URL is an absolute HTTPS endpoint."""
    parsed = urlparse(url)
    return parsed.scheme == "https" and bool(parsed.netloc)


def _expiration_date(payload: Mapping[str, object], domain: str) -> date:
    """Find and parse the expiration event without relying on event order."""
    events = payload.get("events")
    if not isinstance(events, list):
        raise _malformed_response(domain, "RDAP events are missing")

    for event in events:
        if not isinstance(event, Mapping):
            continue
        action = event.get("eventAction")
        if not isinstance(action, str) or action.lower() not in EXPIRATION_ACTIONS:
            continue
        event_date = event.get("eventDate")
        return _parse_event_date(event_date, domain)
    raise _malformed_response(domain, "RDAP expiration event is missing")


def _parse_event_date(value: object, domain: str) -> date:
    """Parse one RFC 3339 RDAP event timestamp into a calendar date."""
    if not isinstance(value, str):
        raise _malformed_response(domain, "Expiration event date is missing")
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise _malformed_response(domain, "Expiration event date is invalid") from exc
    if timestamp.tzinfo is None:
        raise _malformed_response(domain, "Expiration event date lacks a timezone")
    return timestamp.date()


def _raw_status(payload: Mapping[str, object], domain: str) -> tuple[str, ...]:
    """Return RDAP status values without interpreting registry policy."""
    status = payload.get("status", [])
    if not isinstance(status, list) or any(
        not isinstance(item, str) for item in status
    ):
        raise _malformed_response(domain, "RDAP status must be a string list")
    return tuple(status)


def _registrar(payload: Mapping[str, object]) -> str | None:
    """Extract the registrar display name from an RDAP vCard entity."""
    entities = payload.get("entities", [])
    if not isinstance(entities, list):
        return None
    for entity in entities:
        if not isinstance(entity, Mapping):
            continue
        roles = entity.get("roles", [])
        if isinstance(roles, list) and "registrar" in roles:
            return _vcard_name(entity.get("vcardArray"))
    return None


def _vcard_name(vcard: object) -> str | None:
    """Read the formatted-name field from an RDAP jCard value."""
    if not isinstance(vcard, list) or len(vcard) != 2:
        return None
    properties = vcard[1]
    if not isinstance(properties, list):
        return None
    for prop in properties:
        if not isinstance(prop, list) or len(prop) < 4:
            continue
        value = prop[3]
        if prop[0] == "fn" and isinstance(value, str) and value.strip():
            return value.strip()
    return None


def get_domain_expiry(domain: str) -> DomainExpiryResult:
    """Look up a domain and return its expiry date, registrar, and RDAP status.

    Args:
        domain: A bare domain name, optionally Unicode or ending in a root dot.

    Returns:
        A normalized ``DomainExpiryResult`` sourced from the registry's RDAP data.

    Raises:
        DomainLookupError: If input is invalid, the domain is absent, the RDAP
            provider is unavailable, or the response cannot be safely parsed.
    """
    normalized_domain = normalize_domain(domain)
    base_url = _rdap_base_url(normalized_domain)
    lookup_url = f"{base_url.rstrip('/')}/domain/{quote(normalized_domain, safe='')}"
    response = _get_response(lookup_url, normalized_domain, domain_query=True)
    payload = _response_json(response, normalized_domain)
    return DomainExpiryResult(
        domain=normalized_domain,
        expiry_date=_expiration_date(payload, normalized_domain),
        registrar=_registrar(payload),
        raw_status=_raw_status(payload, normalized_domain),
    )
