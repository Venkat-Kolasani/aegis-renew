"""Charge an active Prava mandate and report the checkout outcome."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx

from backend.payments.env import load_local_env
from backend.payments.prava_mandate import (
    PravaConfigurationError,
    PravaMandateError,
    _prava_base_url,
    _prava_secret_key,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MandateChargeCredentials:
    """Single-use credentials from a mandate charge (ephemeral; never persist)."""

    token: str
    dynamic_cvv: str
    expiry_month: str
    expiry_year: str


@dataclass(frozen=True)
class MandateChargeResult:
    """Sanitized + ephemeral result of charging a mandate."""

    mandate_id: str
    transaction_id: str
    order_id: str | None
    status: str
    credentials: MandateChargeCredentials


@dataclass(frozen=True)
class MandateReportResult:
    """Outcome of reporting a mandate charge to Prava."""

    status: str
    mandate_status: str | None
    visa_confirmation: str | None


@dataclass(frozen=True, slots=True)
class ProviderMandate:
    """Validated provider mandate facts with an ephemeral raw identifier."""

    provider_id: str
    customer_id: str | None
    merchant_name: str
    merchant_url: str
    merchant_country: str
    cap_amount: Decimal
    currency: str
    frequency: str
    status: str
    valid_until: datetime


def list_provider_mandates(*, customer_id: str) -> list[ProviderMandate]:
    """Return validated standing mandates for one server-derived customer."""
    load_local_env()
    params = {"standing_only": "true", "customer_id": customer_id}
    try:
        response = httpx.get(
            f"{_prava_base_url()}/v1/mandates",
            headers={"Authorization": f"Bearer {_prava_secret_key()}"},
            params=params,
            timeout=30.0,
        )
    except httpx.TimeoutException as exc:
        raise PravaMandateError("Prava mandate list timed out") from exc
    except httpx.HTTPError as exc:
        raise PravaMandateError("Prava mandate list transport failure") from exc

    if response.status_code >= 400:
        raise PravaMandateError(
            f"Prava mandate list failed (HTTP {response.status_code})",
            status_code=response.status_code,
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise PravaMandateError(
            "Prava mandate list returned a malformed payload"
        ) from exc
    if not isinstance(payload, dict):
        raise PravaMandateError("Prava mandate list returned a malformed payload")
    mandates = payload.get("mandates")
    if not isinstance(mandates, list):
        raise PravaMandateError("Prava mandate list returned a malformed payload")

    parsed = [
        mandate
        for item in mandates
        if isinstance(item, dict)
        and (mandate := _parse_provider_mandate(item)) is not None
    ]
    if mandates and not parsed:
        raise PravaMandateError("Prava mandate list returned no usable mandates")
    return parsed


def _parse_provider_mandate(item: dict[str, Any]) -> ProviderMandate | None:
    """Parse one provider row without retaining unexpected response fields."""
    merchant = _mapping_value(item, "merchant", "merchantDetails", "merchant_details")
    provider_id = _string_value(item, "id", "mandateId", "mandate_id")
    if merchant is None:
        merchant_name = _string_value(item, "merchantName", "merchant_name")
        merchant_url = _string_value(item, "merchantUrl", "merchant_url")
        merchant_country = _string_value(
            item, "merchantCountry", "merchant_country"
        )
    else:
        merchant_name = _string_value(
            merchant, "name", "merchantName", "merchant_name"
        )
        merchant_url = _string_value(
            merchant, "url", "merchantUrl", "merchant_url"
        )
        merchant_country = _string_value(
            merchant,
            "country_code_iso2",
            "countryCodeIso2",
            "merchantCountry",
            "merchant_country",
        )
    cap_amount = _decimal_value(
        item, "capAmount", "cap_amount", "maxAmount", "max_amount", "amount"
    )
    currency = _string_value(item, "currency")
    frequency = _string_value(
        item, "recurringFrequency", "recurring_frequency", "frequency"
    )
    status = _string_value(item, "status")
    valid_until = _datetime_value(
        item, "validUntil", "valid_until", "expiresAt", "expires_at"
    )
    if not all(
        (
            provider_id,
            merchant_name,
            merchant_url,
            merchant_country,
            currency,
            frequency,
            status,
        )
    ) or cap_amount is None or valid_until is None:
        logger.warning("Ignored malformed Prava mandate row")
        return None
    return ProviderMandate(
        provider_id=provider_id,
        customer_id=_string_value(item, "customerId", "customer_id"),
        merchant_name=merchant_name,
        merchant_url=merchant_url,
        merchant_country=merchant_country.upper(),
        cap_amount=cap_amount,
        currency=currency.upper(),
        frequency=frequency.lower(),
        status=status.lower(),
        valid_until=valid_until,
    )


def _mapping_value(item: dict[str, Any], *names: str) -> dict[str, Any] | None:
    """Return the first mapping found under the supplied provider aliases."""
    for name in names:
        value = item.get(name)
        if isinstance(value, dict):
            return value
    return None


def _string_value(item: dict[str, Any], *names: str) -> str:
    """Return one stripped provider string without coercing arbitrary values."""
    for name in names:
        value = item.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _decimal_value(item: dict[str, Any], *names: str) -> Decimal | None:
    """Parse one finite positive provider amount as an exact Decimal."""
    for name in names:
        value = item.get(name)
        try:
            amount = Decimal(str(value))
            quantized = amount.quantize(Decimal("0.01"))
        except (ValueError, TypeError, ArithmeticError):
            continue
        if amount.is_finite() and amount > 0:
            return quantized
    return None


def _datetime_value(item: dict[str, Any], *names: str) -> datetime | None:
    """Parse one provider timestamp and require timezone information."""
    raw = _string_value(item, *names)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def charge_mandate(
    *,
    mandate_id: str,
    amount: Decimal,
    reference: str,
) -> MandateChargeResult:
    """Mint single-use credentials against an active mandate."""
    load_local_env()
    if not mandate_id.startswith("mdt_"):
        raise PravaMandateError("mandate_id must be a Prava mandate id")
    amount_str = f"{amount.quantize(Decimal('0.01')):.2f}"
    try:
        response = httpx.post(
            f"{_prava_base_url()}/v1/mandates/{mandate_id}/charge",
            headers={
                "Authorization": f"Bearer {_prava_secret_key()}",
                "Content-Type": "application/json",
            },
            json={"amount": amount_str, "reference": reference},
            timeout=30.0,
        )
    except httpx.TimeoutException as exc:
        raise PravaMandateError("Prava mandate charge timed out") from exc
    except httpx.HTTPError as exc:
        raise PravaMandateError("Prava mandate charge transport failure") from exc

    if response.status_code >= 400:
        raise PravaMandateError(
            f"Prava mandate charge failed (HTTP {response.status_code})",
            status_code=response.status_code,
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise PravaMandateError(
            "Prava mandate charge returned a malformed payload"
        ) from exc
    if not isinstance(payload, dict):
        raise PravaMandateError("Prava mandate charge returned a malformed payload")

    credentials_raw = payload.get("credentials")
    if not isinstance(credentials_raw, dict):
        raise PravaMandateError("Prava mandate charge missing credentials")

    token = credentials_raw.get("token")
    dynamic_cvv = credentials_raw.get("dynamicCvv") or credentials_raw.get("dynamic_cvv")
    expiry_month = credentials_raw.get("expiryMonth") or credentials_raw.get(
        "expiry_month"
    )
    expiry_year = credentials_raw.get("expiryYear") or credentials_raw.get("expiry_year")
    if not all(
        isinstance(value, str) and value
        for value in (token, dynamic_cvv, expiry_month, expiry_year)
    ):
        raise PravaMandateError("Prava mandate charge credentials incomplete")

    transaction_id = payload.get("transactionId") or payload.get("transaction_id")
    if not isinstance(transaction_id, str) or not transaction_id:
        raise PravaMandateError("Prava mandate charge missing transactionId")

    order_id = payload.get("orderId") or payload.get("order_id")
    status = payload.get("status")
    return MandateChargeResult(
        mandate_id=mandate_id,
        transaction_id=transaction_id,
        order_id=order_id if isinstance(order_id, str) else None,
        status=status if isinstance(status, str) else "awaiting_result",
        credentials=MandateChargeCredentials(
            token=token,
            dynamic_cvv=dynamic_cvv,
            expiry_month=str(expiry_month).zfill(2)[-2:],
            expiry_year=str(expiry_year),
        ),
    )


def report_mandate_charge(
    *,
    mandate_id: str,
    transaction_id: str,
    txn_status: str,
    amount_paid: Decimal | None = None,
) -> MandateReportResult:
    """Report APPROVED or DECLINED for a mandate charge."""
    load_local_env()
    if txn_status not in {"APPROVED", "DECLINED"}:
        raise PravaMandateError("txn_status must be APPROVED or DECLINED")

    body: dict[str, str] = {"txn_status": txn_status, "txn_type": "PURCHASE"}
    if amount_paid is not None:
        body["amount_paid"] = f"{amount_paid.quantize(Decimal('0.01')):.2f}"

    try:
        response = httpx.post(
            f"{_prava_base_url()}/v1/mandates/{mandate_id}/charges/{transaction_id}/report",
            headers={
                "Authorization": f"Bearer {_prava_secret_key()}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=30.0,
        )
    except httpx.TimeoutException as exc:
        raise PravaMandateError("Prava mandate report timed out") from exc
    except httpx.HTTPError as exc:
        raise PravaMandateError("Prava mandate report transport failure") from exc

    if response.status_code >= 400:
        raise PravaMandateError(
            f"Prava mandate report failed (HTTP {response.status_code})",
            status_code=response.status_code,
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise PravaMandateError(
            "Prava mandate report returned a malformed payload"
        ) from exc
    if not isinstance(payload, dict):
        raise PravaMandateError("Prava mandate report returned a malformed payload")

    report_status = payload.get("status")
    visa_confirmation = payload.get("visaConfirmation") or payload.get(
        "visa_confirmation"
    )
    if (
        not isinstance(report_status, str)
        or report_status.strip().lower() not in {"confirmed", "completed", "succeeded"}
        or not isinstance(visa_confirmation, str)
        or visa_confirmation.strip().upper() != "SUCCESS"
    ):
        raise PravaMandateError("Prava mandate report was not confirmed")

    return MandateReportResult(
        status=report_status,
        mandate_status=(
            str(payload["mandateStatus"])
            if isinstance(payload.get("mandateStatus"), str)
            else None
        ),
        visa_confirmation=visa_confirmation,
    )


# Re-export configuration error for callers that only import this module.
__all__ = [
    "MandateChargeCredentials",
    "MandateChargeResult",
    "MandateReportResult",
    "ProviderMandate",
    "PravaConfigurationError",
    "PravaMandateError",
    "charge_mandate",
    "list_provider_mandates",
    "report_mandate_charge",
]
