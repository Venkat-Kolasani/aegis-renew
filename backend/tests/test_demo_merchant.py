"""Unit tests for DEMO registrar quote/checkout and adapter."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from backend.agent.policy import RenewalQuote
from backend.main import create_app
from backend.payments.checkout_adapter import run_demo_mandate_checkout
from backend.payments.demo_merchant import (
    DemoCheckoutError,
    complete_demo_renewal_checkout,
    get_demo_renewal_quote,
)
from backend.payments.prava_charge import (
    MandateChargeCredentials,
    MandateChargeResult,
    MandateReportResult,
    ProviderMandate,
)
from backend.payments.prava_mandate import PravaMandateError

# Visibly synthetic 16-digit stand-in; matches DEMO _TOKEN_PATTERN only.
_SYNTHETIC_TOKEN = "0000000000000000"
_NOW = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


def _provider_mandate() -> ProviderMandate:
    """Return one ephemeral provider mandate for adapter tests."""
    return ProviderMandate(
        provider_id="mdt_test_adapter",
        customer_id="cust_test",
        merchant_name="Aegis Demo Registrar",
        merchant_url="https://example.com",
        merchant_country="US",
        cap_amount=Decimal("18.00"),
        currency="USD",
        frequency="yearly",
        status="active",
        valid_until=_NOW + timedelta(days=365),
    )


def _renewal_quote() -> RenewalQuote:
    """Return one fresh server-derived quote for adapter tests."""
    quote = get_demo_renewal_quote()
    return RenewalQuote(
        domain_id=1,
        merchant_name=quote.merchant_name,
        merchant_url=quote.merchant_url,
        merchant_country=quote.merchant_country,
        amount=quote.amount,
        currency=quote.currency,
        observed_at=_NOW,
    )


def test_demo_quote_is_fixed() -> None:
    quote = get_demo_renewal_quote()
    assert quote.merchant_name == "Aegis Demo Registrar"
    assert quote.amount == Decimal("18.00")
    assert quote.currency == "USD"
    assert "Domain renewal" in quote.product_description


def test_demo_checkout_completes_with_valid_credential_shape() -> None:
    result = complete_demo_renewal_checkout(
        domain="billing.aegis-demo.test",
        token=_SYNTHETIC_TOKEN,
        dynamic_cvv="123",
        expiry_month="12",
        expiry_year="2030",
        amount=Decimal("18.00"),
        currency="USD",
    )
    assert result.completed is True
    assert result.merchant_order_ref.startswith("DEMO-REN-")
    assert result.amount == Decimal("18.00")


def test_demo_checkout_rejects_wrong_amount() -> None:
    with pytest.raises(DemoCheckoutError, match="amount/currency"):
        complete_demo_renewal_checkout(
            domain="billing.aegis-demo.test",
            token=_SYNTHETIC_TOKEN,
            dynamic_cvv="123",
            expiry_month="12",
            expiry_year="2030",
            amount=Decimal("1.00"),
            currency="USD",
        )


def test_demo_checkout_rejects_bad_token_shape() -> None:
    with pytest.raises(DemoCheckoutError, match="16-digit"):
        complete_demo_renewal_checkout(
            domain="billing.aegis-demo.test",
            token="not-a-token",
            dynamic_cvv="123",
            expiry_month="12",
            expiry_year="2030",
            amount=Decimal("18.00"),
            currency="USD",
        )


def test_demo_quote_route(client: TestClient) -> None:
    response = client.get("/api/demo/registrar/quote")
    assert response.status_code == 200
    payload = response.json()
    assert payload["merchant_name"] == "Aegis Demo Registrar"
    assert payload["amount"] == "18.00"
    assert payload["currency"] == "USD"


def test_demo_checkout_credential_route_is_not_browser_accessible(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/demo/registrar/checkout",
        json={
            "domain": "billing.aegis-demo.test",
            "token": _SYNTHETIC_TOKEN,
            "dynamic_cvv": "123",
            "expiry_month": "12",
            "expiry_year": "2030",
            "amount": "18.00",
            "currency": "USD",
        },
    )
    assert response.status_code == 404
    assert _SYNTHETIC_TOKEN not in response.text


def test_adapter_charge_checkout_report_approved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "backend.payments.checkout_adapter.charge_mandate",
        lambda **_: MandateChargeResult(
            mandate_id="mdt_test_adapter",
            transaction_id="txn_test",
            order_id="ord_test",
            status="awaiting_result",
            credentials=MandateChargeCredentials(
                token=_SYNTHETIC_TOKEN,
                dynamic_cvv="123",
                expiry_month="12",
                expiry_year="2030",
            ),
        ),
    )

    reports: list[dict[str, object]] = []

    def fake_report(**kwargs: object) -> MandateReportResult:
        reports.append(kwargs)
        return MandateReportResult(
            status="succeeded",
            mandate_status="active",
            visa_confirmation="SUCCESS",
        )

    monkeypatch.setattr(
        "backend.payments.checkout_adapter.report_mandate_charge",
        fake_report,
    )

    outcome = run_demo_mandate_checkout(
        domain="billing.aegis-demo.test",
        provider_mandate=_provider_mandate(),
        quote=_renewal_quote(),
    )
    assert outcome.completed is True
    assert outcome.payment_status == "completed"
    assert outcome.merchant_order_ref is not None
    assert outcome.merchant_order_ref.startswith("DEMO-REN-")
    assert outcome.prava_report_status == "succeeded"
    assert reports and reports[0]["txn_status"] == "APPROVED"
    assert reports[0]["amount_paid"] == Decimal("18.00")


def test_adapter_preserves_checkout_when_approved_report_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "backend.payments.checkout_adapter.charge_mandate",
        lambda **_: MandateChargeResult(
            mandate_id="mdt_test_adapter",
            transaction_id="txn_test",
            order_id="ord_test",
            status="awaiting_result",
            credentials=MandateChargeCredentials(
                token=_SYNTHETIC_TOKEN,
                dynamic_cvv="123",
                expiry_month="12",
                expiry_year="2030",
            ),
        ),
    )
    monkeypatch.setattr(
        "backend.payments.checkout_adapter.report_mandate_charge",
        lambda **_: (_ for _ in ()).throw(PravaMandateError("report down")),
    )

    outcome = run_demo_mandate_checkout(
        domain="billing.aegis-demo.test",
        provider_mandate=_provider_mandate(),
        quote=_renewal_quote(),
    )
    assert outcome.completed is True
    assert outcome.payment_status == "reconciliation_required"
    assert outcome.merchant_order_ref is not None
    assert outcome.prava_report_status is None
    assert "reconcile" in outcome.detail.lower()


def test_adapter_reports_declined_when_checkout_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "backend.payments.checkout_adapter.charge_mandate",
        lambda **_: MandateChargeResult(
            mandate_id="mdt_test_adapter",
            transaction_id="txn_test",
            order_id=None,
            status="awaiting_result",
            credentials=MandateChargeCredentials(
                token="bad",
                dynamic_cvv="1",
                expiry_month="13",
                expiry_year="xx",
            ),
        ),
    )
    reports: list[dict[str, object]] = []

    def fake_report(**kwargs: object) -> MandateReportResult:
        reports.append(kwargs)
        return MandateReportResult(
            status="failed",
            mandate_status="active",
            visa_confirmation="SUCCESS",
        )

    monkeypatch.setattr(
        "backend.payments.checkout_adapter.report_mandate_charge",
        fake_report,
    )

    outcome = run_demo_mandate_checkout(
        domain="billing.aegis-demo.test",
        provider_mandate=_provider_mandate(),
        quote=_renewal_quote(),
    )
    assert outcome.completed is False
    assert outcome.payment_status == "declined"
    assert outcome.merchant_order_ref is None
    assert outcome.detail == "DEMO merchant checkout was not completed"
    assert reports and reports[0]["txn_status"] == "DECLINED"


def test_adapter_keeps_declined_when_declined_report_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "backend.payments.checkout_adapter.charge_mandate",
        lambda **_: MandateChargeResult(
            mandate_id="mdt_test_adapter",
            transaction_id="txn_test",
            order_id=None,
            status="awaiting_result",
            credentials=MandateChargeCredentials(
                token="bad",
                dynamic_cvv="1",
                expiry_month="13",
                expiry_year="xx",
            ),
        ),
    )
    monkeypatch.setattr(
        "backend.payments.checkout_adapter.report_mandate_charge",
        lambda **_: (_ for _ in ()).throw(PravaMandateError("report down")),
    )

    outcome = run_demo_mandate_checkout(
        domain="billing.aegis-demo.test",
        provider_mandate=_provider_mandate(),
        quote=_renewal_quote(),
    )
    assert outcome.completed is False
    assert outcome.payment_status == "declined_report_failed"
    assert outcome.prava_report_status is None
    assert outcome.detail == "DEMO merchant checkout was not completed"
