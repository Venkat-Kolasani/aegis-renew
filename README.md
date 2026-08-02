# Aegis

Aegis is an agentic infrastructure-renewal prototype for the Prava Agentic
Commerce Hackathon. It detects domain, TLS, and DNS risk, ranks urgency, and
executes a configured renewal only when a user-approved Prava mandate covers it.

## One-minute overview

**Problem.** Domains and certificates expire quietly; dangling DNS can become
subdomain-takeover risk. Teams notice too late and renew manually under pressure.

**What Aegis does.** Scan authorized hosts → explainable LLM ranking →
deterministic coverage policy → charge a **user-approved, merchant-locked
yearly Prava mandate** → complete checkout → show the sanitized audit result.

**Hackathon claim.** Autonomous yearly renewal under a standing mandate in
**Prava sandbox**, with a disclosed **self-owned DEMO registrar** (not a live
Namecheap/etc. storefront). Ranking never spends; only covered execute does.

**Evidence.**
- JOINT-2: [`docs/evidence/joint2-commerce-proof.json`](docs/evidence/joint2-commerce-proof.json)
- VENKAT-3: [`docs/evidence/venkat3-demo-checkout-proof.json`](docs/evidence/venkat3-demo-checkout-proof.json)
- JOINT-3 route smoke: [`docs/evidence/joint3-covered-payment-proof.json`](docs/evidence/joint3-covered-payment-proof.json)
- Demo script: [`docs/DEMO_SCRIPT.md`](docs/DEMO_SCRIPT.md)

## Judge quickstart (local)

```bash
# 1) Python env + deps
python -m venv .venv && source .venv/bin/activate
python -m pip install -r backend/requirements.txt

# 2) Postgres + schema (Docker example)
docker run -d --name aegis-postgres \
  -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=aegis \
  -p 5432:5432 postgres:16
# wait until ready, then:
docker exec -i aegis-postgres psql -U postgres -d aegis < backend/db/schema.sql

# 3) Secrets (never commit)
cp .env.example .env   # fill OPENAI_API_KEY, PRAVA_* sandbox keys, DATABASE_URL

# 4) API + UI
set -a && source .env && set +a
uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
# other terminal:
cd frontend && AEGIS_API_ORIGIN=http://127.0.0.1:8000 npm run dev
# open http://localhost:3000/dashboard

# 5) Cold-start inventory (no manual SQL seeding)
chmod +x scripts/cold_start_demo.sh
AEGIS_API_ORIGIN=http://127.0.0.1:8000 ./scripts/cold_start_demo.sh
```

Then: approve yearly DEMO mandate once → sync → rank → execute covered renewal.
Use Python 3.11+ with `psycopg` installed for Postgres-backed runs and the
gated smoke (`RUN_PRAVA_SMOKE=1`).

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
20 unique domains, at most 100 total caller-supplied items, two history records
per domain, bounded input text, and an output budget that scales from a small
single-result allowance to a 4,096-token ceiling for a full batch.

Invalid caller input does not degrade to a recommendation. A `domain_ids` value
that is not a list of positive, non-boolean integers—or that exceeds the
100-item caller limit—raises `RankingError` with `kind="invalid_input"` before
database or provider access. Missing domains, database or provider failures,
more than 20 unique domains, refusal, truncated output, invalid model output,
and mismatched result IDs retain the conservative `flag_for_review` fallback
without exposing raw error text.

The ranking function is advisory and side-effect-free. The API layer adds a
deterministic, non-spending coverage gate and persists only its final
recommendations:

```bash
curl --fail --request POST http://localhost:8000/api/agent/rank \
  --header 'Content-Type: application/json' \
  --data '{"domain_ids":[1]}'
```

The response contains no coverage or commerce metadata:

```json
[
  {
    "domain_id": 1,
    "criticality_score": 91,
    "decision": "auto_renew",
    "reason": "Domain expiry is imminent."
  }
]
```

The request accepts 1–100 distinct positive integer IDs for already scanned
domains. A batch is ranked once. An `auto_renew` recommendation is retained
only when one active yearly mandate independently matches that domain and the
server-derived DEMO registrar quote's merchant name, canonical HTTPS URL,
country, currency, future validity, and exact `Decimal` amount at or below its
positive cap. Coverage is never assembled from multiple partial mandates. A
missing, inactive, expired, or wrong-domain mandate fails closed, as does a
merchant name, canonical URL, country, yearly frequency, or currency mismatch.
Missing validity, a stale, future-dated, malformed, or non-positive quote, and a
quote above the mandate cap also downgrade only that auto recommendation to
`flag_for_review`; `ignore` and existing `flag_for_review` results are never
upgraded.

The quote is the disclosed fixed DEMO registrar quote observed by the server at
request time. Browsers cannot submit merchant, amount, currency, mandate, or
credential fields to this endpoint. The API writes only sanitized final
`AgentDecision` rows in one transaction and rolls back the whole batch on a
database failure. It does not invoke Prava, mint credentials, execute a renewal,
write payment outcomes, or mutate domain or mandate records. Therefore
`auto_renew` remains a covered recommendation—not proof of spending, a
completed renewal, or permission for a later execution path to skip its own
fresh authorization checks. TLS and DNS observations affect urgency; they are
not separate renewal purchases. Payment execution remains a separate, explicit
API path and is never initiated by ranking.

## Frontend development

```bash
cd frontend
npm run dev
npm run lint
npm run build
npm test
```

### Local two-process UI (VENKAT-4 / VENKAT-5)

Run API and UI together from the repository root (Postgres + `DATABASE_URL` required
for detection routes; `OPENAI_API_KEY` required for live ranking):

```bash
# terminal 1
uvicorn backend.main:app --reload

# terminal 2
cd frontend
# optional: AEGIS_API_ORIGIN=http://localhost:8000
npm run dev
```

Open the dashboard at **http://localhost:3000/dashboard** (marketing home is `/`). Use **Scan now** with a hostname you own or are authorized to
assess. The UI calls same-origin `/aegis-api/scan` → FastAPI `POST /api/scan`,
then refreshes `/aegis-api/domains`. Null expiry fields are partial detector
results, not “healthy.” Mandate setup uses whatever domains are currently stored.

After domains exist, use **Run ranking** in the Agent recommendations panel. That
calls `POST /api/agent/rank` via `/aegis-api/agent/rank`, shows criticality,
decision, and the full reason, and states clearly that ranking is **not** a
payment. `auto_renew` here is a covered recommendation only.

Equivalent curl:

```bash
curl --fail --request POST http://localhost:8000/api/scan \
  --header 'Content-Type: application/json' \
  --data '{"domain":"example.com"}'
curl --fail http://localhost:8000/api/domains
# Use an id from the domains response above; 1 is valid only on an empty database
# that just scanned its first domain.
curl --fail --request POST http://localhost:8000/api/agent/rank \
  --header 'Content-Type: application/json' \
  --data '{"domain_ids":[1]}'
```

The dashboard loads live inventory from `GET /api/domains` (via `/aegis-api`),
shows loading / empty / error / populated states, the yearly mandate panel, and
the decision log. The covered-renewal panel calls `POST /api/payments/execute`
with only the selected domain id and displays `payment_status`,
`merchant_order_ref`, and `completed`; it contains no payment-credential fields.
`npm test` covers DomainList, mandate UI, decision-log and execution states, and
API parser helpers with mocked contract shapes.

The Next.js app proxies `/aegis-api/*` to the FastAPI `/api/*` routes so the
browser never talks to Prava with a secret key.

### Manual sandbox mandate approval (VENKAT-2)

1. Put sandbox keys only in the ignored root `.env`:
   `PRAVA_SANDBOX_BASE_URL`, `PRAVA_PUBLISHABLE_KEY`, `PRAVA_SECRET_KEY`.
   For the Next rewrite, set server-only `AEGIS_API_ORIGIN=http://localhost:8000`
   (defaults to that if unset).
2. Apply schema. Domains appear after **Scan now** (or `POST /api/scan`); mandate
   setup lists whatever rows are stored.
3. Run API + UI: `uvicorn backend.main:app --reload` and `cd frontend && npm run dev`.
4. Open the dashboard → scan at least one domain → **Yearly renewal mandate** →
   Start passkey approval.
5. In the Prava window use the **team** sandbox card details supplied out of
   band and the current sandbox verification instructions from Prava, then
   complete the passkey prompt. Never copy those values into the repository.
6. Confirm the mandate is Active in the Prava dashboard, then select **I
   approved it—sync mandate**. The browser sends only the domain database id;
   the server verifies current provider coverage and persists only its digest
   and sanitized binding metadata.
7. Run ranking again so VIVEK-7 evaluates the newly persisted active coverage.
   Do not paste secret
   keys, network tokens, dynamic CVVs, or raw `mdt_` ids into the repo.

Merchant defaults are the JOINT-2 **DEMO** registrar (`Aegis Demo Registrar`,
`$18/year`). Those merchant, cap, currency, and yearly-frequency values are
display-only fixed product configuration; the only editable setup choice is the
monitored domain. Checkout is completed internally by the VENKAT-3 DEMO adapter,
not a live registrar storefront.

### DEMO registrar checkout (VENKAT-3)

- Quote: `GET /api/demo/registrar/quote` → fixed `$18.00 USD` domain renewal.
- Checkout is an internal server-side DEMO merchant adapter. The former public
  credential-submission route has been removed, so browsers cannot submit a
  token, CVV, expiry, amount, currency, or raw mandate id.
- Adapter: fresh coverage → charge active DEMO mandate → DEMO checkout → validate
  the Prava outcome-report response (`backend/payments/checkout_adapter.py`).
- Unit tests mock Prava and are not transaction proof. The JOINT-3 live sandbox
  smoke is **not** in normal CI and must not run until both teammates review the
  offline tests and sanitized logging:

```bash
RUN_PRAVA_SMOKE=1 python -m pytest backend/tests/test_prava_demo_smoke.py -q
```

Historical VENKAT-3 evidence remains at
[`docs/evidence/venkat3-demo-checkout-proof.json`](docs/evidence/venkat3-demo-checkout-proof.json).
The route smoke writes `docs/evidence/joint3-covered-payment-proof.json` only
after a real successful run; absence of that file means JOINT-3 has not yet been
proved live.

### JOINT-3 covered execution proof (sandbox)

Sanitized evidence: [`docs/evidence/joint3-covered-payment-proof.json`](docs/evidence/joint3-covered-payment-proof.json).

| Step | Result |
|---|---|
| Route | `POST /api/payments/execute` with `{domain_id}` only |
| Fresh coverage recheck | Active yearly DEMO mandate + server DEMO quote `$18 USD` |
| Mandate charge | Real sandbox credentials minted ephemerally |
| Merchant checkout | DEMO registrar completed (`DEMO-REN-…`) |
| Prava report | `APPROVED` confirmed |
| Persistence | `payment_attempts.status=completed` with sanitized order ref |
| Duplicate guard | Second execute returns conflict / already recorded |

Gated smoke (excluded from normal CI; needs interactive mandate already active):

```bash
set -a && source .env && set +a
RUN_PRAVA_SMOKE=1 python -m pytest backend/tests/test_prava_demo_smoke.py -q
```

### Covered payment execution (JOINT-3)

`POST /api/payments/execute` has a strict body containing only `domain_id`:

```bash
curl --fail --request POST http://localhost:8000/api/payments/execute \
  --header 'Content-Type: application/json' \
  --data '{"domain_id":1}'
```

The server reloads the monitored domain and latest final `auto_renew` decision,
fetches a fresh server-owned DEMO renewal quote, and looks up the domain's
provider mandate using its server-derived customer id. It re-runs the VIVEK-7
deterministic policy against one independently complete provider mandate:
domain, active status, merchant name, canonical HTTPS URL, country, yearly
frequency, currency, future validity, and exact `Decimal` amount at or below
the cap must all match. An earlier ranking result alone cannot authorize a
charge.

Execution is serialized per domain with a PostgreSQL advisory lock in
production and an equivalent process lock for SQLite tests. Any existing
authorized, completed, reconciliation-required, or unknown attempt blocks a
second charge; explicitly failed or declined attempts may be retried. Local
persisted coverage and the duplicate guard run before provider lookup, and the
same policy plus provider digest/facts are checked again immediately before the
authorized attempt is created.

An approved provider mandate is reconciled to the existing `Mandate` table with
only a one-way provider-id digest and sanitized binding metadata. The raw
provider mandate id remains ephemeral for the provider call. Post-approval
reconciliation uses `POST /api/payments/mandate/reconcile` with only `domain_id`;
it creates no payment attempt. Uncovered requests create no `PaymentAttempt` and
perform no charge or checkout. Once fresh coverage authorizes a real attempt,
Aegis stores only the domain and mandate foreign keys, server-derived amount,
sanitized merchant order reference, and status.

The response remains exactly:

```json
{
  "payment_status": "completed",
  "merchant_order_ref": "DEMO-REN-…",
  "completed": true
}
```

`completed=true` reflects merchant checkout confirmation. Fully successful
execution also requires Prava's report response to be confirmed. If the merchant
completed but outcome reporting failed or was not confirmed, Aegis preserves
`completed=true` but returns and persists
`payment_status="reconciliation_required"`; it does not present the workflow as
fully successful. Provider, checkout, and database failures use sanitized
responses and never return raw diagnostics or credentials.

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
| Team sandbox card + verification + passkey | Mandate approval completed; card details redacted |
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
- **Product claim:** Aegis keeps **autonomous yearly renewal under a user-approved, merchant-locked Prava mandate**. The DEMO merchant adapter consumes ephemeral Prava credentials server-side; `POST /api/payments/execute` is the only product execution route.
- **DEMO simplifications:** merchant is self-owned (`backend/payments/demo_*`); mandate merchant URL remains `https://example.com`; checkout runs through an internal Aegis adapter, not a third-party registrar.

## Phase 0 — VENKAT-3 DEMO checkout proof

Sanitized evidence: [`docs/evidence/venkat3-demo-checkout-proof.json`](docs/evidence/venkat3-demo-checkout-proof.json).

This historical evidence predates the covered JOINT-3 route. The current smoke
command exercises `POST /api/payments/execute` and writes separate JOINT-3
evidence only after a real success.

| Step | Result |
|---|---|
| Active DEMO mandate | Listed merchant `Aegis Demo Registrar`, yearly, `$18` |
| Mandate charge | Real sandbox credentials minted |
| DEMO merchant checkout | Completed order ref `DEMO-REN-*` |
| Charge report | `APPROVED` to Prava |

### Production access

Sandbox mandate → charge → completed DEMO checkout → `APPROVED` is evidenced
for the hackathon demo. That is **not** authorization to flip to live keys or
real-money production. Production access (if desired later) still requires
Prava’s go-live process: https://tally.so/r/eqBNZE

## Cold-start demo dataset (JOINT-4)

Use [`scripts/cold_start_demo.sh`](scripts/cold_start_demo.sh) against a running
API. It scans six public observation hosts plus the DEMO mandate host
`billing.aegis-demo.test`. It does **not** invent high-confidence takeover
findings against unowned targets.

Uncovered domains (no synced mandate) are expected to rank as
`flag_for_review` / `ignore` — that is the safety path, not a bug. The covered
renewal demo uses the DEMO host after mandate sync.

Skipped / gated tests:

| Test | Status |
|---|---|
| Ordinary backend + frontend suites | Required in CI |
| `backend/tests/test_prava_demo_smoke.py` | Skipped unless `RUN_PRAVA_SMOKE=1` |

## Deployment (JOINT-7)

- Backend blueprint: [`render.yaml`](render.yaml) + [`Dockerfile`](Dockerfile)
  (API + Postgres). Set `OPENAI_API_KEY`, `PRAVA_PUBLISHABLE_KEY`, and
  `PRAVA_SECRET_KEY` only in the Render dashboard.
- Frontend: deploy the `frontend/` Next.js app (e.g. Vercel) with server-only
  `AEGIS_API_ORIGIN=https://<your-aegis-api-host>` so the browser keeps using
  same-origin `/aegis-api/*` rewrites (no Prava secret in the browser).
- Apply `backend/db/schema.sql` once to the provisioned database before first use.
- Never commit `.env`. Free Render web services sleep after idle; cold starts are expected.

## Honest limitations

- Merchant is a **self-owned DEMO registrar**, not a real registrar UCP checkout.
- Mandate merchant URL remains `https://example.com` (disclosed).
- No end-user authentication on payment routes (sandbox/demo-scoped).
- A completed payment attempt currently blocks another charge for that domain
  (demo idempotency; annual renewal cycle needs a later design).
- Live Prava production keys / real-money checkout are out of scope for this
  submission until a real merchant path and auth exist.

## Build disclosure

The Aegis idea and public integration research existed before the event. All
application code in this repository was written during the hackathon build
window. Conceptual inspiration (agent architectures / memory-graph research)
informed the design only; no prior application code was copied into this repo.

Payment claims use real Prava **sandbox** evidence and a completed DEMO merchant
checkout; no payment outcome is mocked. JOINT-2 proved mandate setup and
credential minting. VENKAT-3 proved the disclosed DEMO registrar adapter.
JOINT-3 proved `POST /api/payments/execute` end-to-end with sanitized evidence
at [`docs/evidence/joint3-covered-payment-proof.json`](docs/evidence/joint3-covered-payment-proof.json).

### Tracks

Built to support judging for **Prava Overall**, **OpenAI**, **Visa Intelligent
Commerce**, and **Localhost**. Linq / NANDA / Senso were not added.

### Team

Venkat (frontend + Prava payments) and Vivek (detection + ranking/policy).
