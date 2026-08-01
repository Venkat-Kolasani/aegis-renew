# Aegis — Full Build Prompts for Venkat and Vivek

Ready-to-paste prompts for Cursor, Claude Code, or Codex. Run them in the exact
order below, in separate checkouts/branches where practical. `AGENTS.md` and
`HACKATHON_PLAN.md` are the source of truth; a prompt may not weaken either.

Do not reduce a prompt's requested feature scope. Build each complete feature
prompt quickly with AI, then make **small, coherent, verified commits during
the work** (for example, model + test, then route + test). Update `README.md`
whenever behavior, configuration, manual setup, or evidence changes. Do not
start a dependent prompt until the preceding prompt's full “done when”
condition is met and all its commits are pushed.

Never skip tests, fake a payment result, commit credentials, expose a Prava
secret/network token/dynamic CVV, or alter the locked API contract without an
explicit contract update. The interactive Prava proof is manual because a
passkey is required; it is not a normal CI test.

## Quick reference: order of execution

| ID | Owner | Phase | Depends on |
|---|---|---:|---|
| JOINT-1 | Both | 0 | none |
| JOINT-2 | Venkat drives, Vivek reviews | 0 | JOINT-1 |
| VIVEK-1 | Vivek | 1 | JOINT-1 |
| VIVEK-2 | Vivek | 1 | VIVEK-1 |
| VIVEK-3 | Vivek | 1 | VIVEK-1 |
| VIVEK-4 | Vivek | 1 | VIVEK-1 |
| VIVEK-5 | Vivek | 1 | VIVEK-2, VIVEK-3, VIVEK-4 |
| VENKAT-1 | Venkat | 1 | JOINT-1 |
| VENKAT-2 | Venkat | 1 | JOINT-2 |
| VENKAT-3 | Venkat | 1 | JOINT-2 |
| VENKAT-4 | Venkat | 1/2 | VIVEK-5 |
| VIVEK-6 | Vivek | 2 | VIVEK-5 |
| VIVEK-7 | Vivek | 2 | VIVEK-6 |
| VENKAT-5 | Venkat | 2 | VIVEK-7 |
| JOINT-3 | Both, Venkat drives | 3 | VENKAT-2, VENKAT-3, VIVEK-7 |
| JOINT-4 | Both | 4 | JOINT-3 |
| JOINT-5 | Both | 4 | JOINT-4 |
| JOINT-6 | Both | 4 | JOINT-5 |
| JOINT-7 | Both | 4 | JOINT-5 |
| JOINT-8 | Both | 5 | JOINT-7 |

---

## Phase 0 — JOINT-1: Repository scaffold, contract, and CI

Run together; either person drives and both review the contract.

```text
Read AGENTS.md and HACKATHON_PLAN.md at the repository root. If either is
missing, stop and report it. Do not implement business logic in this prompt.

Ensure the repository has this structure:

aegis/
  backend/
    routes/
    detection/
    agent/
    db/
    tests/
    main.py
    requirements.txt
  frontend/
    (Next.js App Router app, TypeScript, Tailwind)
  .env.example
  .gitignore
  README.md
  AGENTS.md
  HACKATHON_PLAN.md

Do the following:
1. Initialize Git only if required. Use the existing remote and preserve its
   history; do not merge unrelated histories.
2. Scaffold the FastAPI backend with an app factory, health endpoint, and
   detection, agent, and payments routers. Every placeholder must return a
   typed 501 response matching the API contract and have a one-line endpoint
   docstring.
3. Scaffold the Next.js frontend with TypeScript, App Router, Tailwind, and
   ESLint. Keep it a minimal Aegis dashboard shell.
4. Create .env.example with placeholders for DATABASE_URL, OPENAI_API_KEY,
   NEXT_PUBLIC_API_URL, PRAVA_SANDBOX_BASE_URL, PRAVA_PUBLISHABLE_KEY, and
   PRAVA_SECRET_KEY. Never add a public prefix to the secret key.
5. Create .gitignore for .env files, Node/Python build artefacts, coverage,
   and the local hackathon handbook. Confirm no credentials are tracked.
6. Create backend/db/schema.sql for domains, agent_decisions, mandates, and
   payment_attempts. Persist only sanitized status and merchant-order
   references; never store network tokens or dynamic CVVs.
7. Write a README skeleton with project overview, setup, architecture,
   disclosure, team, CI, and the Phase 0 payment-proof gate.
8. Add a FastAPI smoke test and frontend lint/build checks. Add GitHub Actions
   for backend pytest plus frontend lint, tests, and production build.
9. Do not add detection, LLM, or payment business logic in this prompt.

Make focused commits as coherent pieces pass, then run the full local checks.
List files created/changed and report the resulting CI URL.

Done when: the scaffold is pushed, backend smoke tests pass, frontend lint,
tests, and production build pass, CI is green, the worktree is clean, and no
secret file is staged.
```

---

## Phase 0 — JOINT-2: Prava commerce proof and merchant selection gate

Venkat drives; Vivek reviews the evidence and the resulting product claim.

```text
Read AGENTS.md, HACKATHON_PLAN.md, and the installed official
prava-sdk-integration and prava-pay skills, including their references, before
touching payment code. Do not invent endpoint shapes from memory.

1. Confirm sandbox health, test-key access, sandbox test-card access, and a
   WebAuthn-capable browser. Keep keys only in the ignored local .env file.
2. Timebox merchant discovery to one hour. Check Prava merchant discovery,
   UCP Checker, Composio MCP Gateway, and e-commerce MCP directories for a
   real registrar, hosting provider, or SSL vendor with guest checkout or
   UCP/MCP support. Record candidates and links in README without exposing
   secrets.
3. Prove the supported native path end to end: merchant-locked yearly mandate
   approval, active-mandate lookup, mandate charge, completed merchant
   checkout, and outcome reporting. Capture only sanitized evidence: merchant,
   amount/currency, timestamps, completed status, and merchant order reference.
4. A session, approval URL, token, or payment credential alone is not proof.
   The merchant must confirm completed checkout.
5. Do not assume Stripe or another processor accepts a Prava credential. If no
   viable real merchant is found, build a self-owned demo merchant only after
   Prava support or a real sandbox credential proves the checkout path works.
   Mark every such file with a DEMO comment and disclose it in README.
6. If native mandates or merchant completion are unsupported, contact Prava
   support, stop this payment path, and change the product claim to
   per-renewal passkey approval. Never simulate autonomous renewal.

Do not commit credentials, tokens, raw mandate identifiers, or application
payment code here. Commit only sanitized research/evidence/documentation.

Done when: README states the selected merchant and proof result truthfully,
both teammates agree on the product claim, and the proof documentation is
pushed. If the path is unsupported, the README and prompts state the fallback
claim explicitly before any application payment work continues.
```

---

## Phase 1 — Vivek’s track: detection engine

### VIVEK-1: Database models and persistence

```text
Read AGENTS.md before doing anything. You own backend/detection/*,
backend/routes/detection.py, backend/routes/agent.py, backend/agent/*, and
backend/main.py. Do not edit backend/routes/payments.py or frontend files.

Using backend/db/schema.sql, implement:
1. backend/db/connection.py with one consistent database access pattern. Use
   SQLAlchemy engine/session factory if introducing ORM models; do not mix ORM
   and raw psycopg within the same feature. Read DATABASE_URL from the
   environment.
2. backend/db/models.py with models for domains, agent_decisions, mandates,
   and payment_attempts. Match schema types and nullability exactly; payment
   records hold only sanitized references and status.
3. backend/tests/conftest.py fixture that creates an isolated test database.
   SQLite is acceptable for tests if clearly documented; production remains
   Postgres.
4. Tests proving a domain and a decision can be inserted and read back.
5. README setup instructions for creating tables locally.

Use small commits after the database layer and tests pass, then run the full
backend suite.

Done when: database tests pass against the isolated test database, the README
explains local schema setup, and no payment credential can be persisted by the
models.
```

### VIVEK-2: Domain expiry detection through RDAP

```text
Read AGENTS.md before starting. Work only in your ownership area.

In backend/detection/domain_expiry.py:
1. Implement get_domain_expiry(domain: str) -> DomainExpiryResult, using RDAP
   (a direct IANA-bootstrap-backed request or a maintained RDAP client), never
   scraped WHOIS text. Return domain, expiry_date, registrar, and raw_status.
2. Validate the input and create a specific DomainLookupError for not found,
   timeout/unreachable, and malformed response. Log actionable external
   failures; do not let raw exceptions break a batch scan.
3. Add mocked unit tests for a near-expiry domain, a far-future expiry, 404,
   transport failure, and malformed payload.
4. Run a one-off manual lookup for three real domains outside the committed
   test suite. Show the observed output without hardcoding time-varying data.
5. Add a public-function docstring covering parameters and return shape.

Make focused commits after a coherent working implementation and test set,
update README's detection section, then run the full backend suite.

Done when: mocked tests pass, live observations are reported, and the result
and error shapes are usable by the scan route without parsing raw RDAP data.
```

### VIVEK-3: Certificate expiry detection

```text
Read AGENTS.md before starting.

In backend/detection/cert_expiry.py:
1. Implement get_cert_expiry(domain: str) -> CertExpiryResult with domain,
   not_after, issuer, and source. Query crt.sh JSON, choose the latest current
   certificate, and use an expired one only when every discovered certificate
   is expired.
2. Add a direct TLS-handshake fallback using Python ssl when crt.sh is empty or
   unavailable. Return a specific CertLookupError on connection/timeout
   failures rather than crashing other scans.
3. Add mocked tests for multiple certificates, all-expired data, empty/malformed
   data triggering TLS fallback, and transport failure.
4. Run three manual live lookups outside the test suite and report observations.
5. Add a public-function docstring and README architecture note.

Commit in small verified pieces during the work, then run the full backend
suite.

Done when: crt.sh and TLS fallback paths are covered, results name their
source, and a failed certificate lookup degrades gracefully.
```

### VIVEK-4: Dangling-DNS / takeover-risk detection

```text
Read AGENTS.md before starting. This is a judge-visible capability. Test only
domains and subdomains you own or are expressly authorized to probe.

In backend/detection/takeover_risk.py:
1. Implement check_takeover_risk(domain: str) -> TakeoverRiskResult with
   domain, has_dangling_cname, cname_target, matched_service, and confidence.
   Resolve the full CNAME chain using dnspython.
2. Fetch or pin the maintained can-i-take-over-xyz fingerprint source; cite the
   source and refresh date. Do not hand-write fingerprints from memory.
3. On a pattern match, make a lightweight HTTP request to the CNAME target and
   compare against the known unclaimed-resource signature. A pattern match by
   itself is never high confidence.
4. Return high confidence only when pattern and live confirmation both match;
   return pattern_only when the live check is inconclusive.
5. Add mocked tests for a confirmed dangling CNAME, a legitimate live resource,
   and an inconclusive live response.
6. Run manual checks only against two or three authorized subdomains and report
   the result. Do not scan arbitrary public targets for a demo.
7. Add a docstring and README explanation of the false-positive boundary.

Commit coherent verified pieces, then run the full backend suite.

Done when: high-confidence findings have live confirmation, inconclusive cases
are not presented as confirmed compromise, and tests cover the three outcomes.
```

### VIVEK-5: Detection API endpoints

```text
Read AGENTS.md before starting. This is the Phase 1 checkpoint for the
detection track.

In backend/routes/detection.py:
1. Implement POST /api/scan exactly to the contract: accept {domain}, invoke
   all three detection functions, persist scan results, and return the combined
   typed response.
2. Implement GET /api/domains with the contract response shape.
3. Give every endpoint a one-line docstring.
4. Preserve partial data: if RDAP succeeds and crt.sh fails, return the RDAP
   result with a logged null certificate field rather than failing the entire
   request.
5. Add TestClient integration coverage for successful scan, invalid domain,
   storage retrieval, and one mocked detector failure yielding partial results.
6. Update README with curl examples and an honest note on external-service
   failure behavior.

Make small commits as model wiring, endpoints, and integration tests pass.
Run the entire backend suite and notify Venkat only after the contract is live.

Done when: both endpoint shapes are stable, all integration tests pass, and
VENKAT-4 can use the live backend without fixture-specific assumptions.
```

---

## Phase 1 — Venkat’s track: dashboard and Prava

### VENKAT-1: Full dashboard shell and domain-risk presentation

```text
Read AGENTS.md before starting. You own frontend/components/DomainList.tsx,
RiskBadge.tsx, AgentDecisionLog.tsx, MandateSetup.tsx, and the dashboard shell.
Do not edit backend-owned files.

Build the complete presentational dashboard feature:
1. Update app/page.tsx with an Aegis header, dashboard layout, summary area,
   and main domain-risk list. Keep it polished enough for judges.
2. Implement DomainList.tsx as a table of contract-shaped domains: domain,
   expiry_date, cert_expiry_date, dns_risk, and last_scanned. Use six or more
   representative fixture domains until VIVEK-5 is ready.
3. Implement RiskBadge.tsx as a reusable green/yellow/red risk badge using
   days-until-expiry plus DNS risk. DNS takeover risk must be clearly distinct
   from ordinary expiry urgency.
4. Include explicit loading, empty, error-ready, and populated presentation
   states so VENKAT-4 can replace fixtures without redesigning the component.
5. Add render/component tests for empty, loading, populated, healthy,
   near-expiry, and DNS-risk states. Use the smallest appropriate test tooling
   already available; add a dependency only when required for a real render
   check.
6. Update README only for a material UI/setup change. Do not claim live backend
   data before VENKAT-4.

Commit small visual/test slices as they pass, then run frontend tests, lint,
and production build.

Done when: the dashboard convincingly presents all intended risk states using
contract-shaped fixtures, tests cover the states, and the frontend is cleanly
buildable.
```

### VENKAT-2: Prava mandate setup integration

```text
Read AGENTS.md and the official prava-sdk-integration and prava-pay skills plus
their references before writing payment code. Use the official Next.js template
as the starting point for embedded card/passkey UI. Do not guess Prava APIs.
JOINT-2 must have completed successfully first.

Using the verified Prava path from JOINT-2:
1. Build frontend/components/MandateSetup.tsx for a merchant-locked yearly
   renewal mandate. Collect domain selection, merchant name and URL, merchant
   country, cap amount, and currency; do not let the browser choose a mandate
   identifier, network token, dynamic CVV, or final renewal amount.
2. Implement the UI and server route to create the real passkey-approval flow
   using the official SDK/template. POST /api/payments/mandate must match the
   contract exactly: domain_id, merchant_name, merchant_url, merchant_country,
   cap_amount, currency, and frequency=yearly.
3. Handle loading, user cancellation, expiry, and provider failure with clear,
   accessible UI states. Do not leave the page hanging.
4. Add UI tests with mocked official SDK calls for success, cancellation, and
   failure. Keep the interactive sandbox test separate and real.
5. Add README instructions for a judge to configure sandbox keys and manually
   approve the passkey. Never include secret values.

Make small verified commits during implementation, run frontend tests/lint/build,
then perform the interactive sandbox smoke path.

Done when: the real mandate setup route follows the verified Prava contract,
the UI states are tested, and README gives truthful sandbox instructions.
```

### VENKAT-3: Merchant checkout adapter and evidence

```text
Read AGENTS.md and use the selected merchant and evidence from JOINT-2. Do not
repeat speculative merchant discovery; extend its verified result.

1. If JOINT-2 identified a viable real registrar/hosting/SSL merchant, implement
   only the adapter necessary to quote and complete that merchant's renewal
   checkout. Keep merchant identity, country, currency, and renewal product
   server-derived and mandate-bound.
2. If the verified path requires a self-owned demo merchant, build the minimal
   registrar-renewal checkout for a fixed, clearly disclosed product such as
   “Domain renewal — $18/year.” Mark every involved file with a DEMO comment
   and name the substitution plainly in README.
3. Do not add Stripe simply as an assumed fallback. Only use a processor after
   the JOINT-2 evidence proves it accepts the Prava credential in the selected
   environment.
4. Confirm a mandate charge completes the full merchant checkout and report the
   outcome to Prava. Persist/display only sanitized completion evidence.
5. Add a separately marked real sandbox integration smoke test or repeatable
   manual harness. It must assert completed merchant checkout, not session or
   token creation. Do not run it in normal CI.
6. Update README disclosure with the actual merchant choice, evidence location,
   and every demo-only simplification.

Commit small verified pieces, run normal tests, and run the interactive proof
separately.

Done when: the merchant path is real and evidenced, or the README honestly
states the verified limitation and the product claim has been downgraded.
```

### VENKAT-4: Wire the dashboard to live detection data

```text
Read AGENTS.md before starting. VIVEK-5 must be complete; verify the actual
backend response before changing UI code.

1. Replace DomainList fixtures with GET /api/domains, using the contract shape
   without client-side remapping that hides backend mistakes.
2. Add a Scan Now control that calls POST /api/scan for a validated domain and
   refreshes the list on success.
3. Handle loading, error, empty, partial-result, and populated states clearly.
4. Update component tests to mock fetch while retaining the visual-state tests.
5. Manually test the frontend against the local backend with a real authorized
   domain scan; do not claim a result was live if it was fixture data.
6. Update README with the local two-process run instructions and curl/UI demo.

Make small commits as fetch state, scan control, and tests pass. Run the full
frontend suite, lint, and production build.

Done when: the dashboard can scan and display real data from a local backend,
and frontend behavior remains usable when one external detector is unavailable.
```

---

## Phase 2 — recommendations and deterministic payment policy

### VIVEK-6: Structured ranking agent

```text
Read AGENTS.md before starting. Depends on VIVEK-5.

In backend/agent/ranking.py:
1. Implement rank_domains(domain_ids: list[int]) -> list[DecisionResult] with
   domain_id, criticality_score, decision, and reason. Use gpt-4o-mini with
   structured output/function calling; never regex-parse free text.
2. Consider expiry proximity, certificate urgency, confirmed DNS risk, and
   useful historical decision data. Weight confirmed DNS takeover risk heavily.
3. Enforce exactly auto_renew, flag_for_review, or ignore in the output model.
4. Handle OpenAI timeout/rate-limit/provider failures with at most two retries
   and exponential backoff, then produce a conservative flag_for_review result.
5. Add fixed-input tests for urgent, healthy, and ambiguous cases; assert shape
   and clear decisions, never brittle reason wording.
6. Add a docstring and a concise cost-control note in README or code comments.
7. Do not invoke payments, mandates, or on_agent_decision from ranking. Ranking
   is a side-effect-free recommendation only.

Commit in small verified pieces and run the full backend suite.

Done when: ranking returns contract-valid, explainable results under normal and
provider-failure conditions without any possibility of spending money.
```

### VIVEK-7: Deterministic policy and ranking endpoint

```text
Read AGENTS.md before starting. Depends on VIVEK-6.

1. In backend/agent/policy.py, implement deterministic policy that evaluates a
   model decision against the active mandate, merchant binding, current server-
   derived quote, currency, cap, and mandate period. Missing, expired, wrong-
   merchant, stale-price, or insufficient-cap coverage always downgrades to
   flag_for_review.
2. In backend/routes/agent.py, implement POST /api/agent/rank exactly to the
   contract. Call rank_domains, apply policy, and store a sanitized decision
   record with the reason.
3. Keep this endpoint side-effect-free: it records recommendations and cannot
   create a payment, charge a mandate, or call checkout.
4. Add integration tests confirming every downgrade path, especially a model
   auto_renew result that lacks coverage. Add a test proving the rank route
   makes no payment-route call.
5. Add a one-line endpoint docstring and README curl example.

Make small commits as policy cases and endpoint tests pass, then run the full
backend suite.

Done when: model output can never bypass deterministic coverage checks and the
rank endpoint remains demonstrably non-spending.
```

### VENKAT-5: Agent decision-log UI

```text
Read AGENTS.md before starting. Depends on VIVEK-7.

1. Build frontend/components/AgentDecisionLog.tsx to render each domain's
   decision, criticality score, and complete human-readable reason. Do not
   truncate reasoning needed for the demo.
2. Call POST /api/agent/rank and render its response. Make it clear in the UI
   that recommendations are not payment execution.
3. Style all three decisions distinctly and accessibly.
4. Add render tests for auto_renew, flag_for_review, ignore, loading, and API
   error states.
5. Update README/demo notes if user-visible ranking behavior changed.

Make small commits as UI states and tests pass. Run frontend tests, lint, and
production build.

Done when: the dashboard explains every recommendation without implying that a
ranking request itself spent money.
```

---

## Phase 3 — JOINT-3: Covered payment execution wiring

Venkat drives and owns payment files. Vivek reviews the policy/output boundary
before wiring begins.

```text
Read AGENTS.md, especially the payment contract and non-negotiable rules. Read
the official Prava skills again if their documentation changed. This is the
most scrutinized part of the submission.

1. Implement POST /api/payments/mandate and POST /api/payments/execute in
   backend/routes/payments.py using the verified official Prava and merchant
   paths. The mandate route uses the locked mandate request contract. The
   execute route accepts only {domain_id}; it derives merchant, current quote,
   active mandate, cap, currency, and checkout context server-side.
2. Implement on_agent_decision(domain, decision, amount, reason) with the
   locked signature. Only deterministic policy may invoke it, and only after
   active merchant-locked coverage and current price checks pass. Ranking alone
   must never invoke it.
3. Run a real mandate charge and merchant checkout. Return completed=true only
   after the merchant confirms success; record a sanitized merchant order
   reference and status only.
4. Add failure-path integration tests for missing mandate, expired mandate,
   cap exceeded, merchant mismatch, quote mismatch, Prava failure, merchant
   failure, and report-status failure. No test may mock a completed Prava
   transaction as proof.
5. Add one manually invoked end-to-end sandbox smoke test/harness: use an
   approved mandate, invoke covered execution, complete checkout, report the
   outcome, and assert the final merchant completion. Exclude it from ordinary
   CI because it requires a passkey and real sandbox interaction.
6. Update README with exact judge setup, manual proof steps, sanitized evidence,
   and an honest explanation of any demo merchant.

Make small verified commits throughout the implementation. Run ordinary tests
after each safe slice and repeat the interactive proof until both teammates
understand the completed evidence.

Done when: Aegis can execute only a covered, merchant-locked renewal and shows
the merchant’s real completed checkout result end to end. Any uncovered case
is visibly downgraded to review without a payment attempt.
```

---

## Phase 4 — hardening, demo, documentation, deployment

### JOINT-4: Regression data and cold-start pass

```text
Read AGENTS.md before starting.

1. Create a repeatable local/demo dataset of six to eight varied domains:
   healthy, near-expiry, certificate urgent, authorized confirmed DNS-risk,
   pattern-only DNS-risk, and a domain outside the mandate cap. Use real live
   data only where you own/are authorized to scan; never create or imply a
   takeover finding against an unowned target.
2. Run all backend and frontend tests. List flaky or skipped tests explicitly.
3. Run the HACKATHON_PLAN.md demo script from a cold dashboard load. Do not
   manually seed the database on camera.
4. Fix confirmed regressions in focused commits, then run the full suite again.

Done when: the cold-start demo works with no hidden setup, and every limitation
or non-live datum is clearly labeled.
```

### JOINT-5: README, disclosure, and submission material

```text
Read AGENTS.md and HACKATHON_PLAN.md before starting.

Finalize README.md with:
1. A one-minute project overview and problem statement.
2. Reproducible local setup and environment-variable instructions.
3. Architecture and safety-boundary overview.
4. Prava integration: mandate approval, deterministic policy, merchant
   checkout, reporting, and where sanitized completion evidence lives.
5. Disclosure: the idea/research predated the event; all application code was
   written during the build window; list any conceptual inspirations without
   claiming reused code.
6. Track-specific notes for submitted tracks.
7. Honest limitations, demo substitutions, and next steps.

Remove all TODOs and placeholders. Do not exaggerate merchant or payment
capabilities. Commit and push the documentation after both teammates review it.

Done when: a judge can understand, configure, and evaluate the project from
README alone without being misled about any demo-only element.
```

### JOINT-6: Demo video and submission checklist

Process step, not a coding prompt.

Record a two-to-four minute demo that shows real scan data, risk explanation,
the approved mandate, deterministic covered-renewal decision, merchant
checkout completion, and the audit result. State exactly what is live and what
is demo-only. Before submission, walk HACKATHON_PLAN.md's checklist line by
line and preserve the sanitized evidence.

### JOINT-7: Deployment

```text
Read AGENTS.md before starting.

1. Deploy frontend and backend to the agreed hosts. Configure every environment
   variable in the host dashboard; never in the repository or browser bundle.
2. Confirm .env and secrets are absent from both the worktree and Git history.
3. Point the deployed frontend to the deployed backend and rerun the complete
   demo against live URLs, including CORS and error paths.
4. Fix production-only failures in focused commits, then retest.

Done when: the public demo is live, configuration is secure, and the deployed
flow matches the documented limitation and payment evidence.
```

---

## Phase 5 — JOINT-8: Final smoke test

Fifteen minutes before the deadline, run the full backend suite, frontend tests,
lint, production build, secret scan, and one final deployed-demo click-through.
Submit the required materials. Do not add features or refactor in this window.
