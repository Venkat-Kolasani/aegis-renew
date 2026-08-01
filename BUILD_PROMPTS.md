# Aegis - Incremental Build Prompts

Run one prompt at a time. Read `AGENTS.md` first, stay within its ownership
table, run the listed validation, update `README.md` when behavior or setup
changes, then make exactly the requested commit. Do not begin the next prompt
until the current commit is clean and pushed.

## Phase 0 - joint setup

### P0.1 Documentation contract

```text
Read AGENTS.md and HACKATHON_PLAN.md. Verify the payment flow, API contract,
ownership table, deadline, and README disclosure accurately describe Aegis.
Do not edit application code. Fix only stale or contradictory documentation.
Validate that the legacy project name is absent and that no secret-looking values appear
in tracked files. Commit: [docs] align Aegis hackathon plan.
```

### P0.2 Backend shell - Vivek

```text
Read AGENTS.md. Create only the FastAPI app factory, router registration, and
three empty routers: detection, agent, payments. Each placeholder returns a
typed 501 response and has a one-line endpoint docstring. Add a pytest smoke
test that starts TestClient and checks a health endpoint. Update README with
the backend run and test commands. Run pytest. Commit: [backend] scaffold
FastAPI routes and smoke test.
```

### P0.3 Frontend shell - Venkat

```text
Read AGENTS.md. Scaffold frontend/ with Next.js App Router, TypeScript,
Tailwind, and ESLint. Keep the page a minimal Aegis dashboard placeholder;
do not build domain components or payment UI yet. Add the frontend run/build
commands to README. Run npm run lint and npm run build. Commit: [frontend]
scaffold Next.js dashboard shell.
```

### P0.4 Configuration and schema - Vivek

```text
Read AGENTS.md. Add .gitignore, .env.example, and backend/db/schema.sql only.
Use placeholders, never real credentials. The schema contains domains,
agent_decisions, mandates, and payment_attempts with sanitized status and
merchant-order-reference fields only; never persist tokens or dynamic CVVs.
Update README environment-variable and local-database setup. Validate with git
diff --check and a secret scan. Commit: [infra] add env template and schema.
```

### P0.5 CI - Vivek

```text
Read AGENTS.md. Add GitHub Actions that run backend pytest plus frontend lint
and build on push and pull_request. Do not add deployment steps. Run the same
commands locally, update README CI notes, and commit: [ci] add Phase 0 checks.
```

### P0.6 Commerce proof - Venkat

```text
Read the installed official prava-sdk-integration and prava-pay skills plus
AGENTS.md before any payment code. Do not commit credentials or application
payment code. Timebox merchant discovery to one hour, then prove in sandbox:
merchant-locked yearly mandate approval, active mandate lookup, mandate charge,
completed merchant checkout, and outcome report. Record only sanitized evidence
in README. If any step is unsupported, contact Prava support and stop; change
the product claim to per-renewal passkey approval rather than simulating it.
Commit only evidence/documentation changes: [payments] document sandbox proof.
```

## Phase 1 - independent slices

### V1.1 Domain expiry - Vivek

```text
Implement only RDAP domain-expiry lookup and its Pydantic result/error types in
backend/detection/. Use mocked unit tests for success, 404, and malformed data;
run one manually observed live lookup outside the test suite. Update README's
detection section. Commit: [detection] add RDAP expiry lookup.
```

### V1.2 Certificate expiry - Vivek

```text
Implement only crt.sh certificate expiry lookup with direct-TLS fallback and
specific errors. Test current, expired, empty, and transport-failure cases.
Update README and commit: [detection] add certificate expiry lookup.
```

### V1.3 DNS risk - Vivek

```text
Implement only CNAME-chain and maintained fingerprint matching. Confirm a
candidate with lightweight HTTP before high confidence. Test owned/authorized
domains only, document the false-positive boundary, and commit: [detection]
add dangling DNS risk lookup.
```

### V1.4 Scan routes - Vivek

```text
Implement GET /api/domains and POST /api/scan using the locked response shapes.
Partial external failures return available data and a logged null field. Add
TestClient success, invalid-input, and partial-failure tests; update README curl
examples. Commit: [detection] add scan API routes.
```

### N1.1 Domain list - Venkat

```text
Build DomainList and RiskBadge only, using contract-shaped fixture data. Include
empty, loading, and populated render tests. Do not add fetching yet. Update
README screenshots only if the UI is visually changed. Commit: [frontend] add
domain risk list.
```

### N1.2 Live dashboard data - Venkat

```text
After V1.4 is merged, replace fixtures with GET /api/domains and add Scan Now
through POST /api/scan. Test loading, error, and populated states with mocked
fetch. Manually test against the local backend. Commit: [frontend] connect
dashboard scan data.
```

## Phase 2 - recommendations, not spending

### V2.1 Structured ranking - Vivek

```text
Implement rank_domains with gpt-4o-mini structured output. It returns one of
auto_renew, flag_for_review, or ignore with a human-readable reason, but it
does not trigger payments. Test output shape and OpenAI failure fallback.
Update README and commit: [agent] add structured domain ranking.
```

### V2.2 Deterministic payment policy - Vivek

```text
Implement policy that checks active mandate, merchant binding, and current
price against the cap. Missing or insufficient coverage always becomes
flag_for_review. Test every downgrade path. Commit: [agent] add renewal safety
policy.
```

### N2.1 Decision log - Venkat

```text
Build AgentDecisionLog only after V2.1 is merged. Render all decisions and
reasons; ranking requests must not spend. Add render tests and commit:
[frontend] add decision log.
```

## Phase 3 - payment integration

### N3.1 Mandate setup - Venkat

```text
Implement the approved native mandate UI and server route using only official
Prava references. Never expose secret keys, raw mandate IDs, network tokens,
or CVVs. Unit-test UI states with mocked SDK calls and update README sandbox
instructions. Commit: [payments] add mandate setup flow.
```

### N3.2 Covered renewal - Venkat

```text
Implement POST /api/payments/execute with domain_id only. Resolve all payment
context server-side, run checkout, report the outcome, and store sanitized
evidence. Add failure-path integration tests. Run the interactive sandbox smoke
test separately and document its result. Commit: [payments] execute covered
renewal.
```

## Final gate

```text
Run backend tests, frontend lint/build/tests, secret scan, and a cold-start
manual demo. Update README disclosure, deployment instructions, demo evidence,
and known limitations. Make no feature changes in the final 15 minutes.
```
