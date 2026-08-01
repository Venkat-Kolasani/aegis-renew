"""Create merchant-locked yearly Prava mandate-setup sessions.

Uses the verified JOINT-2 sandbox path: POST /v1/sessions with mandate_setup.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

import httpx

from backend.payments.env import load_local_env

logger = logging.getLogger(__name__)


class PravaConfigurationError(RuntimeError):
    """Raised when required Prava sandbox configuration is missing."""


class PravaMandateError(RuntimeError):
    """Raised when Prava mandate-session creation fails."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class MandateSessionResult:
    """Sanitized fields needed to continue passkey approval in the browser."""

    session_id: str
    iframe_url: str
    expires_at: str | None
    order_id: str | None


def _prava_base_url() -> str:
    load_local_env()
    base = os.getenv("PRAVA_SANDBOX_BASE_URL", "").strip().rstrip("/")
    if not base:
        raise PravaConfigurationError("PRAVA_SANDBOX_BASE_URL is required")
    parsed = urlparse(base)
    if parsed.scheme != "https" or not parsed.netloc:
        raise PravaConfigurationError(
            "PRAVA_SANDBOX_BASE_URL must be an https URL with a host"
        )
    return base


def _prava_secret_key() -> str:
    load_local_env()
    secret = os.getenv("PRAVA_SECRET_KEY", "").strip()
    if not secret or not secret.startswith("sk_"):
        raise PravaConfigurationError("PRAVA_SECRET_KEY must be a sk_test_/sk_live_ key")
    if secret.startswith("sk_live_"):
        logger.warning("Using a live Prava secret key outside the preferred sandbox path")
    return secret


def create_yearly_mandate_session(
    *,
    domain: str,
    merchant_name: str,
    merchant_url: str,
    merchant_country: str,
    cap_amount: Decimal,
    currency: str,
    user_id: str,
    user_email: str,
    timeout_seconds: float = 30.0,
) -> MandateSessionResult:
    """Create a yearly listed-scope mandate-setup session and return the approval URL.

    Parameters:
        domain: Monitored domain label used only in product description text.
        merchant_name: Destination merchant display name (mandate lock).
        merchant_url: Destination merchant https URL (mandate lock).
        merchant_country: ISO 3166-1 alpha-2 country code.
        cap_amount: Per-charge cap as Decimal (NUMERIC(12, 2) consistent).
        currency: ISO 4217 currency code.
        user_id: Stable customer id for Prava.
        user_email: Customer email for Prava.
        timeout_seconds: HTTP timeout for the Prava API call.

    Returns:
        MandateSessionResult with iframe_url for passkey approval.
    """
    try:
        quantized = Decimal(cap_amount).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise PravaMandateError("cap_amount must be a valid decimal amount") from exc
    if quantized <= 0:
        raise PravaMandateError("cap_amount must be greater than zero")
    amount = f"{quantized:.2f}"
    country = merchant_country.strip().upper()
    currency_code = currency.strip().upper()
    body = {
        "user_id": user_id,
        "user_email": user_email,
        "total_amount": amount,
        "currency": currency_code,
        "description": f"Aegis yearly renewal mandate for {domain}",
        "purchase_context": [
            {
                "merchant_details": {
                    # DEMO: JOINT-2 selected a self-owned demo registrar until a
                    # real registrar with guest/UCP checkout is available.
                    "name": merchant_name,
                    "url": merchant_url,
                    "country_code_iso2": country,
                    "category_code": "7372",
                    "category": "Computer Programming Services",
                },
                "product_details": [
                    {
                        "description": f"Domain renewal — 1 year ({domain})",
                        "unit_price": amount,
                        "quantity": 1,
                    }
                ],
                "effective_until_minutes": 60,
            }
        ],
        "integration_type": "full_checkout",
        "mandate_setup": {
            "intent": "mandate_setup",
            "recurring_frequency": "yearly",
            "merchant_scope": "listed",
            "max_charges": 5,
        },
    }

    try:
        response = httpx.post(
            f"{_prava_base_url()}/v1/sessions",
            headers={
                "Authorization": f"Bearer {_prava_secret_key()}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=timeout_seconds,
        )
    except httpx.TimeoutException as exc:
        logger.error("Prava mandate session timed out")
        raise PravaMandateError("Prava mandate session timed out") from exc
    except httpx.HTTPError as exc:
        logger.error("Prava mandate session transport failure: %s", type(exc).__name__)
        raise PravaMandateError("Prava mandate session transport failure") from exc

    if response.status_code >= 400:
        message = _error_message(response)
        logger.error(
            "Prava mandate session failed status_code=%s",
            response.status_code,
        )
        raise PravaMandateError(message, status_code=response.status_code)

    try:
        payload = response.json()
    except ValueError as exc:
        raise PravaMandateError("Prava returned a malformed session payload") from exc

    if not isinstance(payload, dict):
        raise PravaMandateError("Prava returned a malformed session payload")

    session_id = payload.get("session_id")
    iframe_url = payload.get("iframe_url")
    if not isinstance(session_id, str) or not session_id:
        raise PravaMandateError("Prava session response missing session_id")
    if not isinstance(iframe_url, str) or not iframe_url.startswith("https://"):
        raise PravaMandateError("Prava session response missing iframe_url")

    expires_at = payload.get("expires_at")
    order_id = payload.get("order_id")
    return MandateSessionResult(
        session_id=session_id,
        iframe_url=iframe_url,
        expires_at=expires_at if isinstance(expires_at, str) else None,
        order_id=order_id if isinstance(order_id, str) else None,
    )


def _error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return f"Prava mandate session failed (HTTP {response.status_code})"
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        message = error.get("message")
        code = error.get("code")
        if isinstance(message, str) and message:
            return f"{code}: {message}" if code else message
    return f"Prava mandate session failed (HTTP {response.status_code})"
