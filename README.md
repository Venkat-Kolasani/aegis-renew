# Aegis

Aegis is an agentic infrastructure-renewal prototype for the Prava Agentic
Commerce Hackathon. It detects domain, TLS, and DNS risk, ranks urgency, and
executes a configured renewal only when a user-approved Prava mandate covers it.

## Backend development

Create a Python 3.11+ virtual environment, install the backend dependencies,
then run the API from the repository root:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r backend/requirements.txt
uvicorn backend.main:app --reload
```

Run the complete backend suite from the repository root:

```bash
python -m pytest backend/tests -q
```

The API exposes `GET /health` plus the persisted detection routes documented
below. Set up Postgres and export `DATABASE_URL` as described in
[Configuration and database](#configuration-and-database) before starting it.

### Domain expiry detection

`get_domain_expiry()` validates and normalizes a bare domain, uses IANA's DNS
RDAP bootstrap registry to select the authoritative service, and returns a
typed expiration date, registrar name when published, and raw RDAP statuses.
It never scrapes WHOIS text.

Invalid input, missing domains, provider timeouts/transport failures, and
malformed RDAP responses raise a classified `DomainLookupError`. Callers can
log or isolate one failed lookup without losing successful results from a
larger scan. Unit tests mock every HTTP request and require no internet access.

### Certificate expiry detection

`get_cert_expiry()` validates a bare hostname and queries crt.sh JSON as its
primary source. It filters certificates by exact or single-label wildcard
coverage, deduplicates results, and selects the latest currently valid match;
when every matching certificate is expired, it returns the latest expired one.

If crt.sh is unavailable, times out, or returns no usable certificate data,
Aegis performs a bounded direct TLS handshake with SNI and reports that result
with `source="tls"`. Invalid input, TLS timeouts, unreachable endpoints,
malformed peer data, and missing certificates raise a classified
`CertLookupError`, allowing future batch scans to isolate failures gracefully.
Domain renewal does not automatically renew, replace, or repair a TLS
certificate; TLS expiry is an independent risk signal.

### Dangling-CNAME and takeover-risk detection

`check_takeover_risk()` inspects only the exact authorized hostname supplied by
the caller and follows its CNAME chain for at most 10 hops. It never enumerates
or brute-forces subdomains. Targets are compared at DNS-label boundaries with
vulnerable entries from EdOverflow's canonical
[`can-i-take-over-xyz`](https://github.com/EdOverflow/can-i-take-over-xyz)
fingerprints, pinned to commit
`5bd4e12837911c8475486f1da922c9b9c706e632` (refresh: 2025-02-08).

A vulnerable provider match alone returns `confidence="pattern_only"`. High
confidence requires either the upstream HTTP fingerprint in one bounded live
response or an exact target NXDOMAIN when upstream defines NXDOMAIN as its
fingerprint. Timeouts, blocked/private targets, provider errors, truncated
responses, unexpected HTTP 5xx responses, and ambiguous results remain
`pattern_only`; a successful nonmatching response can return
`confidence="none"` for a live resource.

`has_dangling_cname=true` means a vulnerable provider pattern was found; it is
not a claim of compromise. `pattern_only` is not `dns_risk`, and even high
confidence is strong evidence rather than proof of exploitability. Later API
integration must set confirmed `dns_risk=true` only for `confidence="high"`.
Fingerprints can become stale and provider behavior can change, so results
still require authorized human review. Address preflight checks reduce SSRF
risk but cannot eliminate DNS rebinding between validation and the HTTP
connection. Run live probing only for hostnames you own or are expressly
authorized to assess. Aegis detects risk but never registers, claims, modifies,
or exploits provider resources.

### Detection API

Apply `backend/db/schema.sql`, export the Postgres `DATABASE_URL`, and start the
backend before calling these routes. List stored scans:

```bash
curl --fail http://localhost:8000/api/domains
```

Scan and persist one exact authorized hostname:

```bash
curl --fail --request POST http://localhost:8000/api/scan \
  --header 'Content-Type: application/json' \
  --data '{"domain":"owned.example.com"}'
```

`POST /api/scan` normalizes the hostname, runs RDAP, certificate, and takeover
detection independently, then atomically upserts the available results. A
classified external-service failure uses a safe null or false default for a new
domain. On later scans it preserves that detector's last-known-good fields while
updating successful detector facts and `last_scanned`. RDAP, crt.sh, direct TLS,
DNS, the pinned fingerprint source, and HTTP confirmation can still time out,
rate-limit, or return incomplete data.

`dns_risk=true` is stored only for `confidence="high"`, which is strong live
evidence requiring review rather than proof of compromise. `pattern_only` is
reported as uncertain detail and persists `dns_risk=false`. Run takeover-risk
scans only for hostnames you own or are explicitly authorized to assess; the
endpoint does not enumerate other subdomains.

### Structured domain ranking

`rank_domains(domain_ids)` reads only the requested domains' normalized name,
domain and certificate expiry proximity, DNS-risk flag and bounded sanitized
detail, plus at most two recent recommendations per domain. It submits those
allowlisted facts in one batch to `gpt-4o-mini` through the OpenAI SDK's strict
Structured Outputs parsing and returns one typed result per requested ID:
`domain_id`, integer `criticality_score` from 0–100, `decision` (`auto_renew`,
`flag_for_review`, or `ignore`), and a bounded reason. Set `OPENAI_API_KEY` in
the backend process environment; never commit it.

The SDK's own retries are disabled. Aegis makes at most three total attempts,
retrying only transient connection, timeout, rate-limit, and retryable server
failures with bounded backoff. Cost controls are one batched request for up to
20 unique domains, two history records per domain, bounded input text, and a
2,000-output-token ceiling. Missing records, database failures, provider
failures, refusals, malformed structured output, or mismatched result IDs
degrade to a standardized `flag_for_review` result without exposing raw error
text.

Ranking is advisory, side-effect-free, and cannot spend money: it reads facts
but does not persist decisions, invoke renewal execution, or mutate domain
state. TLS and DNS observations affect urgency; they are not separate renewal
purchases. Deterministic mandate, merchant, quote, currency, and cap checks
come later, so a recommendation is not proof of spending authority or a
successful renewal.

## Frontend development

```bash
cd frontend
npm run dev
npm run lint
npm run build
npm test
```

The dashboard currently renders six-plus contract-shaped domain fixtures with
green/yellow/red expiry urgency and a distinct DNS takeover signal, plus the
yearly mandate setup panel. `npm test` covers empty, loading, error, healthy,
near-expiry, urgent, DNS-risk, and mandate idle/success/cancel/failure states;
live detection data is added in VENKAT-4.

The Next.js app proxies `/aegis-api/*` to the FastAPI `/api/*` routes so the
browser never talks to Prava with a secret key.

### Manual sandbox mandate approval (VENKAT-2)

1. Put sandbox keys only in the ignored root `.env`:
   `PRAVA_SANDBOX_BASE_URL`, `PRAVA_PUBLISHABLE_KEY`, `PRAVA_SECRET_KEY`.
   For the Next rewrite, set server-only `AEGIS_API_ORIGIN=http://localhost:8000`
   (defaults to that if unset).
2. Apply schema and insert at least one domain row matching a fixture id, e.g.
   `billing.aegis-demo.test` as id `2` (mandate setup looks up `domain_id`).
3. Run API + UI: `uvicorn backend.main:app --reload` and `cd frontend && npm run dev`.
4. Open the dashboard → **Yearly renewal mandate** → Start passkey approval.
5. In the Prava window use the **team** sandbox card (expiry `12/30`) and the
   sandbox OTP from the Prava docs/team email when asked, then complete the
   passkey prompt.
6. Confirm the mandate is Active in the Prava dashboard. Do not paste secret
   keys, network tokens, dynamic CVVs, or raw `mdt_` ids into the repo.

Merchant defaults are the JOINT-2 **DEMO** registrar (`Aegis Demo Registrar`,
`$18/year`). Checkout is completed by the VENKAT-3 DEMO adapter
(`POST /api/demo/registrar/checkout`), not a live registrar storefront.

### DEMO registrar checkout (VENKAT-3)

- Quote: `GET /api/demo/registrar/quote` → fixed `$18.00 USD` domain renewal.
- Checkout: `POST /api/demo/registrar/checkout` accepts a Prava network token +
  dynamic CVV shape, returns a sanitized `merchant_order_ref`, and never stores
  credentials.
- Adapter: charge active DEMO mandate → DEMO checkout → report `APPROVED` to
  Prava (`backend/payments/checkout_adapter.py`).
- Unit tests mock Prava. The live sandbox smoke is **not** in normal CI:

```bash
RUN_PRAVA_SMOKE=1 python -m pytest backend/tests/test_prava_demo_smoke.py -q
```

Sanitized smoke evidence (when run): [`docs/evidence/venkat3-demo-checkout-proof.json`](docs/evidence/venkat3-demo-checkout-proof.json).

## Configuration and database

Copy `.env.example` to `.env` and fill local values only. Export
`DATABASE_URL` into the backend process environment; the SQLAlchemy production
engine requires PostgreSQL through the psycopg driver. `PRAVA_SECRET_KEY` is
server-only; never use a public browser environment-variable prefix for it.

Create a local Postgres database and apply the approved shared schema:

```bash
createdb aegis
export DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:5432/aegis"
psql "postgresql://postgres:postgres@localhost:5432/aegis" -f backend/db/schema.sql
```

Application persistence uses SQLAlchemy 2.x ORM models and sessions. Tests use
a fresh temporary SQLite database per test, so they never touch the configured
Postgres database.

The schema keeps detection results, recommendations, mandate metadata, and
sanitized payment outcomes. Models contain no card information, payment
credentials, network tokens, dynamic CVVs, or secret fields.

Security note: the shared Phase 0 SQL currently names a required column
`provider_mandate_id`, which conflicts with the project rule forbidding raw
Prava mandate identifiers. Until schema ownership is handed over, the ORM maps
that column to `provider_mandate_id_digest` and rejects non-digest values. The
shared SQL has not been changed; it should later be renamed explicitly so the
database-level name also communicates the security boundary. This digest is
for correlation only and cannot be used to execute a payment; a later payment
phase must use an explicitly approved provider-side lookup or secret-management
design without adding a raw identifier to these models.

## Continuous integration

Every push and pull request runs backend `pytest`, frontend render tests, lint,
and a frontend production build through GitHub Actions. Run the same commands
locally before pushing a build slice.

## Phase 0 — JOINT-2 commerce proof

Sanitized evidence: [`docs/evidence/joint2-commerce-proof.json`](docs/evidence/joint2-commerce-proof.json).

### What was proved in Prava sandbox (2026-08-01)

| Step | Result |
|---|---|
| Sandbox health | `ok` at `https://sandbox.api.prava.space/health` |
| Test API keys | `pk_test_` / `sk_test_` accepted |
| Team sandbox card + OTP + passkey | Mandate approval completed (Visa last4 `2564`) |
| Merchant-locked **yearly** mandate | **Active** — cap `$18.00 USD`, scope `listed`, merchant `Aegis Demo Registrar` |
| Active-mandate lookup | Returned via `GET /v1/mandates?customer_id=…` |
| Mandate charge | Credentials minted (`token` + `dynamicCvv` + expiry); status `awaiting_result` |
| Merchant checkout | **Not completed** in JOINT-2 (placeholder URL; no registrar checkout) |
| Charge report | `DECLINED` reported honestly — Visa confirmation `SUCCESS`, mandate remains `active` |

Dashboard observation matched the API: order **Authorized**, mandate **Active / Yearly / $18.00**.

### Merchant discovery (1-hour timebox)

| Source | Finding |
|---|---|
| Registrar/SSL targets (`namecheap.com`, `porkbun.com`, `godaddy.com`, `hover.com`, `name.com`, …) | No usable `/.well-known/ucp` commerce manifest |
| [UCP Checker](https://ucpchecker.com/) | Index is overwhelmingly Shopify storefronts |
| Prava docs — [UCP](https://docs.prava.space/integration/ucp) + [Browser Harness](https://docs.prava.space/integration/browser-harness) | Agent checkout path is **Shopify-specific**, not registrars |
| Composio / e-commerce MCP directories | No domain-renewal registrar with guest agent checkout identified |

### Selected merchant path and product claim

- **Selected path:** self-owned **DEMO** registrar merchant (domain renewal — `$18/year`), clearly marked `# DEMO:` / `// DEMO:` in code and named here.
- **Why:** no real registrar/hosting/SSL vendor with UCP/guest agent checkout was found; the interim mandate destination `https://example.com` cannot run a third-party checkout.
- **Product claim:** Aegis keeps **autonomous yearly renewal under a user-approved, merchant-locked Prava mandate**. DEMO checkout accepts a real Prava network token; full product `POST /api/payments/execute` lands in JOINT-3.
- **DEMO simplifications:** merchant is self-owned (`backend/payments/demo_*`); mandate merchant URL remains `https://example.com`; checkout runs in-process via Aegis routes, not a third-party registrar.

## Phase 0 — VENKAT-3 DEMO checkout proof

Sanitized evidence: [`docs/evidence/venkat3-demo-checkout-proof.json`](docs/evidence/venkat3-demo-checkout-proof.json).

Re-run: `RUN_PRAVA_SMOKE=1 python -m pytest backend/tests/test_prava_demo_smoke.py -q`

| Step | Result |
|---|---|
| Active DEMO mandate | Listed merchant `Aegis Demo Registrar`, yearly, `$18` |
| Mandate charge | Real sandbox credentials minted |
| DEMO merchant checkout | Completed order ref `DEMO-REN-*` |
| Charge report | `APPROVED` to Prava |

### Production access

Sandbox mandate → charge → completed DEMO checkout → `APPROVED` is evidenced.
You may now submit the Prava production form if you want prod keys: https://tally.so/r/eqBNZE

## Build disclosure

The Aegis idea and public integration research existed before the event. All
application code in this repository is being written during the hackathon build
window. Payment claims require real Prava sandbox evidence and a completed
merchant checkout; no payment outcome is mocked. JOINT-2 proved mandate setup
and credential minting. VENKAT-3 adds the disclosed DEMO registrar checkout
path and a CI-excluded sandbox smoke that asserts completed checkout + `APPROVED`.
