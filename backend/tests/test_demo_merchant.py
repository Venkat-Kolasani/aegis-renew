"""Unit tests for DEMO registrar quote/checkout and adapter."""

from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

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
)
from backend.payments.prava_mandate import PravaMandateError

# Visibly synthetic 16-digit stand-in; matches DEMO _TOKEN_PATTERN only.
_SYNTHETIC_TOKEN = "0000000000000000"


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


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


def test_demo_checkout_route_success(client: TestClient) -> None:
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
    assert response.status_code == 200
    payload = response.json()
    assert payload["completed"] is True
    assert payload["merchant_order_ref"].startswith("DEMO-REN-")
    # Credentials must never echo back.
    assert "token" not in payload
    assert "cvv" not in payload
    assert "dynamic_cvv" not in payload


def test_demo_checkout_route_rejects_bad_amount(client: TestClient) -> None:
    response = client.post(
        "/api/demo/registrar/checkout",
        json={
            "domain": "billing.aegis-demo.test",
            "token": _SYNTHETIC_TOKEN,
            "dynamic_cvv": "123",
            "expiry_month": "12",
            "expiry_year": "2030",
            "amount": "9.99",
            "currency": "USD",
        },
    )
    assert response.status_code == 400


def test_demo_checkout_route_rejects_oversized_amount(client: TestClient) -> None:
    response = client.post(
        "/api/demo/registrar/checkout",
        json={
            "domain": "billing.aegis-demo.test",
            "token": _SYNTHETIC_TOKEN,
            "dynamic_cvv": "123",
            "expiry_month": "12",
            "expiry_year": "2030",
            "amount": "1" * 33,
            "currency": "USD",
        },
    )
    assert response.status_code == 422


def test_adapter_charge_checkout_report_approved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "backend.payments.checkout_adapter.find_active_demo_mandate",
        lambda **_: {"id": "mdt_test_adapter", "status": "active"},
    )
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
        customer_id="cust_test",
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
        customer_id="cust_test",
        mandate_id="mdt_test_adapter",
    )
    assert outcome.completed is True
    assert outcome.payment_status == "completed"
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
        customer_id="cust_test",
        mandate_id="mdt_test_adapter",
    )
    assert outcome.completed is False
    assert outcome.payment_status == "declined"
    assert outcome.merchant_order_ref is None
    assert outcome.detail  # original DemoCheckoutError message
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
        customer_id="cust_test",
        mandate_id="mdt_test_adapter",
    )
    assert outcome.completed is False
    assert outcome.payment_status == "declined"
    assert outcome.prava_report_status is None
    assert "16-digit" in outcome.detail or "Payment token" in outcome.detail
