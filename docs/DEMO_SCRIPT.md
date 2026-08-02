# Aegis demo video + submission checklist (JOINT-6)

Target length: 2–4 minutes. Record against sandbox + disclosed DEMO merchant.

## Spoken outline

1. **Problem (15s)** — Domains/TLS expire; dangling DNS is takeover risk. Manual renewal is late and error-prone.
2. **Scan (30s)** — Cold dashboard → Scan now on owned/authorized hosts from `scripts/cold_start_demo.sh`. Show live expiry/cert fields. Say nulls are partial detectors, not “healthy.”
3. **Rank (30s)** — Run ranking. Show criticality + full reason. Say ranking never spends money.
4. **Mandate (30s)** — Show yearly DEMO mandate already approved (or approve once with passkey). Sync mandate. Browser sends only `domain_id`.
5. **Execute (45s)** — Covered renewal panel → Execute. Show `payment_status=completed`, `merchant_order_ref=DEMO-REN-…`, `completed=true`.
6. **Honesty (20s)** — Live: Prava sandbox mandate charge + report. Demo-only: self-owned DEMO registrar checkout (`https://example.com`), not Namecheap/etc. Built during the hackathon window.

## On-camera do-nots

- Do not paste secrets, tokens, CVVs, or raw `mdt_` / `txn_` ids
- Do not manually `INSERT` rows on camera; use Scan / cold-start script beforehand if needed
- Do not claim a real registrar checkout

## Submission checklist

- [ ] README disclosure complete
- [ ] `docs/evidence/joint2-commerce-proof.json` present
- [ ] `docs/evidence/venkat3-demo-checkout-proof.json` present
- [ ] `docs/evidence/joint3-covered-payment-proof.json` present (route smoke)
- [ ] Backend tests green; frontend tests/lint/build green
- [ ] Demo video uploaded
- [ ] Deployed URLs (if any) use host-dashboard secrets only
- [ ] Tracks claimed match what actually runs (Prava / OpenAI / Visa / Localhost)
