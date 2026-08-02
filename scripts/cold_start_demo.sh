#!/usr/bin/env bash
# JOINT-4 cold-start inventory: scan a fixed, authorized demo set via the live API.
# Does not seed the database by hand and never claims DNS takeover on public hosts.
set -euo pipefail

API_ORIGIN="${AEGIS_API_ORIGIN:-http://127.0.0.1:8001}"
API_ORIGIN="${API_ORIGIN%/}"

# Public domains for live RDAP/TLS signals (authorized demo observation only).
# DEMO hostname is product-local and labeled as such in the UI/README.
DOMAINS=(
  "example.com"
  "google.com"
  "github.com"
  "cloudflare.com"
  "wikipedia.org"
  "python.org"
  "billing.aegis-demo.test"
)

echo "==> Health check ${API_ORIGIN}/health"
curl --fail --silent "${API_ORIGIN}/health" | python3 -m json.tool

echo "==> Scanning ${#DOMAINS[@]} domains (live detectors; DEMO host may lack RDAP/TLS)"
for domain in "${DOMAINS[@]}"; do
  echo "--- scan ${domain}"
  curl --fail --silent --request POST "${API_ORIGIN}/api/scan" \
    --header 'Content-Type: application/json' \
    --data "{\"domain\":\"${domain}\"}" | python3 -m json.tool
done

echo "==> Inventory"
curl --fail --silent "${API_ORIGIN}/api/domains" > /tmp/aegis_domains.json
python3 - <<'PY'
import json
from pathlib import Path
rows = json.loads(Path("/tmp/aegis_domains.json").read_text())
print(f"count={len(rows)}")
for row in rows:
    print(
        f"{row['id']:>3} {row['domain']:<28} "
        f"expiry={row.get('expiry_date')} cert={row.get('cert_expiry_date')} "
        f"dns_risk={row.get('dns_risk')}"
    )
print()
print("Next (not automated here):")
print("1) Approve yearly DEMO mandate for billing.aegis-demo.test and sync")
print("2) Rank domain ids from the inventory")
print("3) Execute covered renewal only when latest decision is auto_renew")
print("4) State clearly: merchant checkout is the disclosed DEMO registrar")
PY
