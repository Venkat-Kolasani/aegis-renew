"""Offline unit tests for validated Prava mandate provider boundaries."""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest

from backend.payments import prava_charge
from backend.payments.prava_mandate import PravaMandateError


def _response(payload: object, *, status_code: int = 200) -> httpx.Response:
    """Build one detached HTTPX JSON response."""
    return httpx.Response(
        status_code,
        json=payload,
        request=httpx.Request("GET", "https://sandbox.example/v1/mandates"),
    )


@pytest.fixture(autouse=True)
def provider_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace configuration lookups without reading local credentials."""
    monkeypatch.setattr(prava_charge, "load_local_env", lambda: None)
    monkeypatch.setattr(prava_charge, "_prava_base_url", lambda: "https://sandbox.example")
    monkeypatch.setattr(prava_charge, "_prava_secret_key", lambda: "sk_test_redacted")


def test_list_provider_mandates_parses_only_allowlisted_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider lookup returns typed metadata while ignoring credential-like fields."""
    monkeypatch.setattr(
        prava_charge.httpx,
        "get",
        lambda *args, **kwargs: _response(
            {
                "mandates": [
                    {
                        "id": "mdt_ephemeral",
                        "customerId": "aegis_domain_1",
                        "merchantName": "Aegis Demo Registrar",
                        "merchantUrl": "https://example.com",
                        "merchantCountry": "US",
                        "capAmount": "25.00",
                        "currency": "USD",
                        "recurringFrequency": "yearly",
                        "status": "active",
                        "validUntil": "2030-08-02T12:00:00Z",
                        "token": "must-not-be-carried",
                    }
                ]
            }
        ),
    )

    result = prava_charge.list_provider_mandates(customer_id="aegis_domain_1")

    assert len(result) == 1
    mandate = result[0]
    assert mandate.provider_id == "mdt_ephemeral"
    assert mandate.cap_amount == Decimal("25.00")
    assert mandate.frequency == "yearly"
    assert not hasattr(mandate, "token")


def test_list_provider_mandates_parses_official_docs_shaped_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Official list fields (approvedAmount, sparse merchant) remain usable."""
    monkeypatch.setattr(
        prava_charge.httpx,
        "get",
        lambda *args, **kwargs: _response(
            {
                "mandates": [
                    {
                        "id": "mdt_123",
                        "status": "active",
                        "state": "available",
                        "recurringFrequency": "yearly",
                        "merchantScope": "listed",
                        "merchantName": "Aegis Demo Registrar",
                        "approvedAmount": "18.00",
                        "remaining": "18.00",
                        "currency": "USD",
                        "validUntil": "2031-07-31T08:44:37.392Z",
                        "renewsAt": "2027-08-01T08:44:37.392Z",
                    }
                ]
            }
        ),
    )

    result = prava_charge.list_provider_mandates(customer_id="aegis_domain_1")

    assert len(result) == 1
    mandate = result[0]
    assert mandate.provider_id == "mdt_123"
    assert mandate.cap_amount == Decimal("18.00")
    assert mandate.merchant_name == "Aegis Demo Registrar"
    assert mandate.merchant_url == "https://example.com"
    assert mandate.merchant_country == "US"
    assert mandate.frequency == "yearly"
    assert mandate.status == "active"


def test_list_provider_mandates_rejects_sparse_non_demo_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sparse non-DEMO rows without URL/country stay unusable."""
    monkeypatch.setattr(
        prava_charge.httpx,
        "get",
        lambda *args, **kwargs: _response(
            {
                "mandates": [
                    {
                        "id": "mdt_other",
                        "status": "active",
                        "merchantName": "Other Merchant",
                        "approvedAmount": "18.00",
                        "currency": "USD",
                        "recurringFrequency": "yearly",
                        "validUntil": "2031-07-31T08:44:37.392Z",
                    }
                ]
            }
        ),
    )

    with pytest.raises(PravaMandateError, match="no usable"):
        prava_charge.list_provider_mandates(customer_id="aegis_domain_1")


def test_list_provider_mandates_rejects_unusable_provider_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-empty malformed provider response is an unavailable lookup."""
    monkeypatch.setattr(
        prava_charge.httpx,
        "get",
        lambda *args, **kwargs: _response({"mandates": [{"id": "mdt_only"}]}),
    )

    with pytest.raises(PravaMandateError, match="no usable"):
        prava_charge.list_provider_mandates(customer_id="aegis_domain_1")


def test_report_mandate_charge_requires_confirmed_visa_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTP success alone cannot turn outcome reporting into success."""
    monkeypatch.setattr(
        prava_charge.httpx,
        "post",
        lambda *args, **kwargs: _response(
            {"status": "pending", "visaConfirmation": "FAILED"}
        ),
    )

    with pytest.raises(PravaMandateError, match="not confirmed"):
        prava_charge.report_mandate_charge(
            mandate_id="mdt_ephemeral",
            transaction_id="txn_ephemeral",
            txn_status="APPROVED",
            amount_paid=Decimal("18.00"),
        )


def test_report_mandate_charge_accepts_confirmed_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A confirmed report with Visa success returns sanitized status."""
    monkeypatch.setattr(
        prava_charge.httpx,
        "post",
        lambda *args, **kwargs: _response(
            {
                "status": "completed",
                "mandateStatus": "active",
                "visaConfirmation": "SUCCESS",
            }
        ),
    )

    result = prava_charge.report_mandate_charge(
        mandate_id="mdt_ephemeral",
        transaction_id="txn_ephemeral",
        txn_status="APPROVED",
        amount_paid=Decimal("18.00"),
    )

    assert result.status == "completed"
    assert result.mandate_status == "active"
    assert result.visa_confirmation == "SUCCESS"
