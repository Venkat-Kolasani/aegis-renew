"""Real Prava sandbox smoke: mandate charge → DEMO checkout → APPROVED report.

Excluded from normal CI. Run manually:

    RUN_PRAVA_SMOKE=1 python -m pytest backend/tests/test_prava_demo_smoke.py -q

Requires root `.env` with sandbox keys and an active DEMO-merchant yearly mandate.
Writes sanitized evidence to docs/evidence/venkat3-demo-checkout-proof.json
(override with PRAVA_SMOKE_EVIDENCE_PATH for a scratch path).
Never prints network tokens, CVVs, or raw mandate ids.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.payments.checkout_adapter import run_demo_mandate_checkout
from backend.payments.demo_constants import DEMO_MERCHANT_NAME, DEMO_RENEWAL_AMOUNT
from backend.payments.env import load_local_env
from backend.payments.prava_charge import find_active_demo_mandate, list_active_mandates
from backend.payments.prava_mandate import PravaConfigurationError, PravaMandateError

pytestmark = pytest.mark.smoke

_REPO_ROOT = Path(__file__).resolve().parents[2]
_EVIDENCE_PATH = _REPO_ROOT / "docs" / "evidence" / "venkat3-demo-checkout-proof.json"


def _resolved_evidence_path() -> Path:
    override = os.environ.get("PRAVA_SMOKE_EVIDENCE_PATH")
    if override:
        return Path(override)
    return _EVIDENCE_PATH


def _smoke_enabled() -> bool:
    return os.environ.get("RUN_PRAVA_SMOKE") == "1"


@pytest.mark.skipif(not _smoke_enabled(), reason="Set RUN_PRAVA_SMOKE=1 for live sandbox")
def test_demo_mandate_checkout_reports_approved() -> None:
    """Charge an active DEMO mandate, complete DEMO checkout, report APPROVED."""
    load_local_env()

    try:
        active = list_active_mandates()
    except PravaConfigurationError as exc:
        pytest.fail(f"Prava is not configured: {exc}")
    except PravaMandateError as exc:
        pytest.fail(f"Could not list mandates: {exc}")

    demo = next(
        (
            item
            for item in active
            if item.get("merchantName") == DEMO_MERCHANT_NAME
            and item.get("status") == "active"
        ),
        None,
    )
    if demo is None:
        pytest.fail(
            "No active Aegis Demo Registrar mandate found. "
            "Complete VENKAT-2 mandate approval first."
        )

    customer_id = demo.get("customerId")
    mandate_id = demo.get("id")
    if not isinstance(customer_id, str) or not isinstance(mandate_id, str):
        pytest.fail("Active DEMO mandate missing customerId or id")

    # Confirm finder also resolves by customer (without asserting on raw ids).
    found = find_active_demo_mandate(
        customer_id=customer_id,
        merchant_name=DEMO_MERCHANT_NAME,
    )
    assert found is not None
    assert found.get("merchantName") == DEMO_MERCHANT_NAME

    outcome = run_demo_mandate_checkout(
        domain="billing.aegis-demo.test",
        customer_id=customer_id,
        mandate_id=mandate_id,
    )

    assert outcome.completed is True, outcome.detail
    assert outcome.payment_status == "completed"
    assert outcome.merchant_order_ref is not None
    assert outcome.merchant_order_ref.startswith("DEMO-REN-")
    assert outcome.amount == DEMO_RENEWAL_AMOUNT
    assert outcome.currency == "USD"
    assert outcome.prava_report_status  # non-empty; exact string may vary

    evidence = {
        "proof_id": "VENKAT-3",
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "environment": "sandbox",
        "merchant": {
            "path": "self_owned_demo_merchant",
            "name": DEMO_MERCHANT_NAME,
            "product": "Domain renewal — $18/year",
            "checkout_surface": "POST /api/demo/registrar/checkout (DEMO stand-in)",
        },
        "flow": {
            "mandate_charge": "credentials_minted",
            "merchant_checkout_completed": True,
            "merchant_order_ref_prefix": "DEMO-REN-",
            "amount": f"{outcome.amount:.2f}",
            "currency": outcome.currency,
            "prava_report": "APPROVED",
            "prava_report_status": outcome.prava_report_status,
        },
        "notes": [
            "No network token, dynamic CVV, or raw mdt_/txn ids are stored.",
            "This smoke is interactive/manual and excluded from normal CI.",
        ],
    }
    evidence_path = _resolved_evidence_path()
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
