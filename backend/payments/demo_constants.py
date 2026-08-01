"""DEMO: Self-owned Aegis Demo Registrar product constants.

JOINT-2 found no real registrar with UCP/guest agent checkout. This module is
the disclosed stand-in for a domain-renewal merchant (Domain renewal — $18/year).
"""

from __future__ import annotations

from decimal import Decimal

# DEMO: fixed renewal SKU for the hackathon merchant stand-in.
DEMO_MERCHANT_NAME = "Aegis Demo Registrar"
DEMO_MERCHANT_URL = "https://example.com"
DEMO_MERCHANT_COUNTRY = "US"
DEMO_PRODUCT_DESCRIPTION = "Domain renewal — 1 year"
DEMO_RENEWAL_AMOUNT = Decimal("18.00")
DEMO_CURRENCY = "USD"
