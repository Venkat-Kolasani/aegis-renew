"""Charge an active Prava mandate and report the checkout outcome."""

from __future__ import annotations

import logging
from dataclasses import dataclass
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


def list_active_mandates(*, customer_id: str | None = None) -> list[dict[str, Any]]:
    """List standing mandates and return active ones (ids for server use only)."""
    load_local_env()
    params: dict[str, str] = {"standing_only": "true"}
    if customer_id:
        params["customer_id"] = customer_id
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

    payload = response.json()
    if not isinstance(payload, dict):
        raise PravaMandateError("Prava mandate list returned a malformed payload")
    mandates = payload.get("mandates")
    if not isinstance(mandates, list):
        return []
    active = [
        item
        for item in mandates
        if isinstance(item, dict) and item.get("status") == "active"
    ]
    return active


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

    payload = response.json()
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

    payload = response.json()
    if not isinstance(payload, dict):
        raise PravaMandateError("Prava mandate report returned a malformed payload")

    return MandateReportResult(
        status=str(payload.get("status") or ""),
        mandate_status=(
            str(payload["mandateStatus"])
            if isinstance(payload.get("mandateStatus"), str)
            else None
        ),
        visa_confirmation=(
            str(payload["visaConfirmation"])
            if isinstance(payload.get("visaConfirmation"), str)
            else None
        ),
    )


def find_active_demo_mandate(
    *,
    customer_id: str,
    merchant_name: str,
) -> dict[str, Any] | None:
    """Find an active listed mandate for the DEMO merchant by display name."""
    for mandate in list_active_mandates(customer_id=customer_id):
        if mandate.get("merchantName") == merchant_name and mandate.get("status") == "active":
            return mandate
    return None


# Re-export configuration error for callers that only import this module.
__all__ = [
    "MandateChargeCredentials",
    "MandateChargeResult",
    "MandateReportResult",
    "PravaConfigurationError",
    "PravaMandateError",
    "charge_mandate",
    "find_active_demo_mandate",
    "list_active_mandates",
    "report_mandate_charge",
]
