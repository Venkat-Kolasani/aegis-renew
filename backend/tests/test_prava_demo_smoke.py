"""Opt-in JOINT-3 route smoke against Prava sandbox and the DEMO merchant.

Excluded from normal CI. Run only after both teammates review offline tests and
sanitized logging:

    RUN_PRAVA_SMOKE=1 python -m pytest backend/tests/test_prava_demo_smoke.py -q

The configured database must already contain the monitored DEMO domain, an
approved mandate synced through the product reconciliation action, and a latest
final ``auto_renew`` decision produced by the normal ranking path. A successful
run writes new sanitized JOINT-3 evidence. It never prints or writes credentials,
raw mandate ids, or provider transaction ids.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.db.connection import DatabaseConfigurationError, get_session_factory
from backend.db.models import AgentDecision, Domain, Mandate, PaymentAttempt
from backend.main import create_app
from backend.payments.demo_constants import DEMO_MERCHANT_NAME
from backend.payments.env import load_local_env

pytestmark = pytest.mark.smoke

_REPO_ROOT = Path(__file__).resolve().parents[2]
_EVIDENCE_PATH = _REPO_ROOT / "docs" / "evidence" / "joint3-covered-payment-proof.json"
_DEMO_DOMAIN = "billing.aegis-demo.test"


def _resolved_evidence_path() -> Path:
    """Return the optional scratch or default sanitized evidence path."""
    override = os.environ.get("PRAVA_SMOKE_EVIDENCE_PATH")
    return Path(override) if override else _EVIDENCE_PATH


def _smoke_enabled() -> bool:
    """Return whether the explicitly manual live smoke was requested."""
    return os.environ.get("RUN_PRAVA_SMOKE") == "1"


def _covered_demo_domain_id() -> int:
    """Require an existing DEMO domain whose latest final decision is auto-renew."""
    factory = get_session_factory()
    with factory() as session:
        domain = session.scalar(select(Domain).where(Domain.domain == _DEMO_DOMAIN))
        if domain is None:
            pytest.fail(f"Scan {_DEMO_DOMAIN} before the JOINT-3 smoke")
        latest = session.scalar(
            select(AgentDecision)
            .where(AgentDecision.domain_id == domain.id)
            .order_by(AgentDecision.created_at.desc(), AgentDecision.id.desc())
            .limit(1)
        )
        if latest is None or latest.decision != "auto_renew":
            pytest.fail("Run ranking and obtain a final auto_renew decision first")
        return domain.id


@pytest.mark.skipif(not _smoke_enabled(), reason="Set RUN_PRAVA_SMOKE=1 for live sandbox")
def test_joint3_execute_route_completes_covered_renewal() -> None:
    """Exercise execute → coverage → charge → checkout → report → persistence."""
    load_local_env()
    try:
        domain_id = _covered_demo_domain_id()
    except DatabaseConfigurationError as exc:
        pytest.fail(f"Database is not configured ({type(exc).__name__})")

    with TestClient(create_app()) as client:
        response = client.post("/api/payments/execute", json={"domain_id": domain_id})

    assert response.status_code == 200, "Covered payment execution did not succeed"
    payload = response.json()
    assert payload == {
        "payment_status": "completed",
        "merchant_order_ref": payload["merchant_order_ref"],
        "completed": True,
    }
    assert isinstance(payload["merchant_order_ref"], str)
    assert payload["merchant_order_ref"].startswith("DEMO-REN-")

    factory = get_session_factory()
    with factory() as session:
        attempt = session.scalar(
            select(PaymentAttempt)
            .where(PaymentAttempt.domain_id == domain_id)
            .order_by(PaymentAttempt.created_at.desc(), PaymentAttempt.id.desc())
            .limit(1)
        )
        assert attempt is not None
        assert attempt.status == "completed"
        assert attempt.merchant_order_ref == payload["merchant_order_ref"]
        mandate = session.get(Mandate, attempt.mandate_id)
        assert mandate is not None
        assert mandate.provider_mandate_id_digest.startswith("sha256:")

    evidence = {
        "proof_id": "JOINT-3",
        "recorded_at_utc": datetime.now(UTC).isoformat(),
        "environment": "sandbox",
        "route": "POST /api/payments/execute",
        "merchant": {
            "path": "self_owned_demo_merchant",
            "name": DEMO_MERCHANT_NAME,
            "product": "Domain renewal — $18/year",
        },
        "flow": {
            "fresh_coverage_rechecked": True,
            "mandate_charge": "credentials_minted_ephemerally",
            "merchant_checkout_completed": True,
            "merchant_order_ref": payload["merchant_order_ref"],
            "prava_report": "APPROVED_CONFIRMED",
            "payment_attempt_persisted": True,
        },
        "notes": [
            "Generated only by the manually enabled live route smoke.",
            "No credentials, raw mandate ids, or provider transaction ids are stored.",
        ],
    }
    evidence_path = _resolved_evidence_path()
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
