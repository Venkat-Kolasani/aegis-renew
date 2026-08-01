# AGENTS.md

This file is read by AI coding agents (Cursor, Claude Code, Codex, or any other
agent working in this repo). Follow it exactly. If an instruction here conflicts
with your own defaults, this file wins.

## Project

Aegis: an agent that detects expiring domains, TLS certificates, and dangling
DNS records (subdomain takeover risk), ranks them by criticality using an LLM,
and autonomously executes renewal payments through Prava when a user-approved
mandate covers the action.

Built for the Agentic Commerce Hackathon (Prava), a ~44 hour build window from
July 31 to August 2, 2026, by a 2 person team: Venkat and Vivek. Optimize for
working software over completeness. A smaller feature that fully works beats a
larger one that half works.

## Non-Negotiable Rules From the Hackathon Handbook

These override normal hackathon-speed shortcuts. Read before writing code:

1. **No mocked transactions.** Every Prava call in the payment flow (session
   creation, passkey approval, mandate creation, payment token issuance,
   checkout completion) must be a real call against Prava's sandbox. Never
   hardcode a "success" response or skip a step to save time. The handbook
   states this explicitly as grounds for disqualification, not just a weaker
   demo.
2. **Show the completed checkout result, not just a created session.** A
   payment session or token alone does not count as a completed transaction.
   The flow must run all the way through to a real checkout success response
   from the merchant.
3. **Only code written during the build window counts as judged work.**
   Conceptual inspiration from prior projects (memory-graph work, agent
   architectures) is fine and should be disclosed in the README. Do not copy
   code from prior repos into this one.
4. **Never commit credentials.** No API keys, Prava credentials, or OpenAI
   keys in the repo at any point. `.env` goes in `.gitignore` from the first
   commit. Judges may be given repo access.
5. **Disclose simplifications.** If a merchant is self-built rather than a
   real registrar (see Merchant Integration below), that must be clearly
   commented in code and stated in the README, not hidden.

## Before Writing Any Prava Code

Do not write Prava API calls from memory or general training knowledge. Prava
is a real, actively changing product and hallucinated endpoints will waste
hours. Before touching anything payment-related:

1. Fetch and read `https://github.com/Prava-Payments/prava-skills`
2. Read both `prava-sdk-integration` and `prava-pay`, including their
   `SKILL.md` and `references/` folders. The SDK controls embedded card/passkey
   UI; native mandate lifecycle is documented in the agent skill.
3. Use the official Next.js SDK template as the starting point for embedded
   card/passkey UI. Do not invent mandate endpoints or assume a session is a
   mandate.
4. Complete a sandbox mandate and merchant-checkout proof before implementing
   any product payment route. If Prava support cannot confirm the required
   native-mandate path, change the product claim to per-renewal approval; never
   fake autonomous renewal.

## Merchant Integration

Before building a merchant integration, check Prava's merchant discovery
resources (Prava Merchant List, UCP Checker, Composio MCP Gateway,
e-commerce MCP directory) for a real registrar, hosting, or SSL vendor with
guest checkout or UCP/MCP support. A real merchant is stronger evidence of a
real transaction than a self-built one. Do not assume Stripe or any other
processor accepts a Prava network token. A self-built fallback is allowed only
after a real sandbox credential completes that merchant's checkout; otherwise
get a supported path from Prava support. Mark every demo-merchant file with a
`# DEMO:` or `// DEMO:` comment explaining what it stands in for.

## Ownership Boundaries

Two people are working in this repo simultaneously. Do not edit files outside
your assigned owner's scope even if it seems convenient. If a task requires
touching another owner's file, stop and flag it instead of editing directly.

| Path | Owner | Notes |
|---|---|---|
| `backend/routes/detection.py` | Vivek | Domain/cert/DNS scanning endpoints |
| `backend/routes/agent.py` | Vivek | Criticality ranking + decision endpoints |
| `backend/routes/payments.py` | Venkat | Prava session/mandate/payment endpoints |
| `backend/main.py` | Vivek | App factory and router registration |
| `backend/detection/*` | Vivek | RDAP, crt.sh, DNS/CNAME fingerprint logic |
| `backend/agent/*` | Vivek | OpenAI function-calling ranking logic |
| `frontend/components/DomainList.tsx` | Venkat | |
| `frontend/components/RiskBadge.tsx` | Venkat | |
| `frontend/components/AgentDecisionLog.tsx` | Venkat | |
| `frontend/components/MandateSetup.tsx` | Venkat | Prava passkey/mandate UI |
| `backend/db/schema.sql` | Shared, single owner per edit | Never edit concurrently, confirm with the other person first |
| `.env.example` | Shared | Update immediately when adding any new env var |

This split favors workload predictability over strict skill-matching: the
detection track involves constant verification against messy real-world data
(RDAP, crt.sh, DNS edge cases), the frontend/Prava track is largely built
against an official template and a documented sandbox. Adjust if Vivek's
bandwidth or preference differs.

The one genuinely shared integration point is the function that connects an
agent decision to a Prava payment trigger. Venkat owns the file. Vivek's side
of the contract is the function signature below, do not change its shape
without updating this file.

```python
def on_agent_decision(domain: str, decision: str, amount: float, reason: str) -> None:
    """
    decision is one of: "auto_renew", "flag_for_review", "ignore"
    amount is the renewal cost in USD
    reason is a short human-readable string for the decision log
    """
```

## Tech Stack, Do Not Substitute Without Discussion

- Backend: FastAPI (Python 3.11+)
- Frontend: Next.js (App Router) + Tailwind CSS
- Database: Postgres, accessed via SQLAlchemy or raw `psycopg`, whichever is
  already in use in the file you're editing, do not mix both in the same file
- LLM: OpenAI API, `gpt-4o-mini` for ranking calls by default, escalate to
  `gpt-4o` only if a ranking call clearly needs it
- Payments: Prava SDK per the official skill repo, see section above
- Detection libraries: `dnspython`, `requests`, RDAP client (`whoisit` or
  equivalent), crt.sh queried via its public JSON endpoint
- Testing: `pytest` for backend, `pytest-asyncio` for async endpoint tests,
  `Playwright` or `Vitest` + `React Testing Library` for frontend

## API Contract

Do not change these shapes without updating this file and notifying the other
owner. This contract is what allows both people to work independently.

```
GET  /api/domains
  -> [{ id, domain, expiry_date, cert_expiry_date, dns_risk: bool, last_scanned }]

POST /api/scan
  body: { domain: string }
  -> { id, domain, expiry_date, cert_expiry_date, dns_risk, dns_risk_detail }

POST /api/agent/rank
  body: { domain_ids: [int] }
  -> [{ domain_id, criticality_score: 0-100, decision: "auto_renew" | "flag_for_review" | "ignore", reason: string }]
  -> Side-effect-free: ranking never charges a mandate.

POST /api/payments/mandate
  body: { domain_id: int, merchant_name: string, merchant_url: string,
          merchant_country: string, cap_amount: number, currency: string,
          frequency: "yearly" }
  -> { status, approval_url }

POST /api/payments/execute
  body: { domain_id: int }
  -> { payment_status, merchant_order_ref, completed: bool }
```

The browser never sends a Prava mandate id, amount, network token, dynamic CVV,
or checkout credential. The payment service derives them from server-side
domain and mandate records. `on_agent_decision` remains the internal contract;
only deterministic policy may call it after verifying a matching active mandate,
merchant binding, and a current price within the cap.

## Detection Engine Specifics

- Domain expiry: use RDAP, not scraped WHOIS. RDAP is JSON, has no rate-limit
  scraping risk, and is the modern IANA-endorsed replacement for WHOIS.
- Certificate expiry: query crt.sh's public JSON endpoint for the domain,
  parse `not_after`. Cross-check with a direct TLS handshake (`ssl` module in
  Python) against the live cert if crt.sh data looks stale.
- Dangling CNAME / takeover risk: resolve the domain's CNAME chain with
  `dnspython`, compare the target against the public `can-i-take-over-xyz`
  fingerprint list (search for the current maintained JSON/README on GitHub,
  do not hand-roll a fingerprint list from memory), and if a pattern matches,
  attempt a lightweight HTTP GET against the target to confirm the resource is
  actually unclaimed before flagging it as a real risk, not just a pattern
  match. False positives here undermine the demo's credibility.

## Code Quality Standards

- Type hints on every function signature, Python and TypeScript both
- Pydantic models for all FastAPI request/response bodies, no raw dicts
- Every external call (RDAP, crt.sh, DNS, OpenAI, Prava) wrapped in a
  try/except with a specific, loggable failure mode, external services will
  fail or rate-limit during a live demo, plan for it
- No bare `except:`, catch specific exceptions
- No print statements for debugging left in committed code, use `logging`
- Functions stay under roughly 40 lines, extract helpers rather than nesting

## Testing Requirements

Every phase ends with tests passing before moving to the next phase, not
after everything is built. Specifically:

- Every detection function (RDAP lookup, crt.sh lookup, CNAME/fingerprint
  match) gets a `pytest` unit test using a mocked HTTP response fixture, plus
  at least one test run against a real live domain to confirm the mock
  matches reality
- Every FastAPI endpoint gets an integration test via `TestClient` covering
  the success path and at least one failure path (bad input, external
  service down)
- The ranking agent gets a test with a fixed, known input asserting the
  output JSON matches the contract shape, not asserting on exact wording
- The Prava payment flow gets one true end-to-end sandbox smoke test that runs
  mandate approval through completed checkout. It is manually invoked and
  excluded from normal CI because passkeys require an interactive browser;
  save sanitized completion evidence and never mock its success result.
- Frontend components get a render test confirming they don't crash on empty,
  loading, and populated states

## Documentation Requirements

- Every new backend endpoint gets a one-line docstring stating what it does
  and what it returns, no exceptions, this is what makes the API contract
  self-verifying instead of just a promise in this file.
- Update the root `README.md` incrementally as features land, not all at the
  end. A judge should be able to run the project from README alone, and the
  README must include the disclosure section required by the hackathon rules
  (what existed before the event, what was built during it).
- Every commit message states what changed and which owner's area it
  touched, e.g. `[detection] add crt.sh cert expiry lookup`, `[payments] wire
  Prava mandate creation`. This makes it trivial to spot if someone
  accidentally edited outside their ownership boundary.
- Any self-built merchant simplification must be called out with a `# DEMO:` or
  `// DEMO:` comment at the point it's used, so it's never mistaken for
  production-real by a teammate skimming the code later, and must also be
  named plainly in the README.

## Definition of Done, Per Phase

- Phase 1 done when: `/api/domains` and `/api/scan` return real data for at
  least 5 real domains with a mix of near-expiry and healthy states, unit and
  integration tests pass, and the frontend dashboard shell renders it (even
  with rough styling)
- Phase 2 done when: `/api/agent/rank` returns a structured decision for
  every scanned domain with a human-readable reason and a passing test, and
  the mandate setup UI can complete a real passkey approval against Prava's
  sandbox
- Phase 3 done when: deterministic policy finds an active, merchant-locked
  mandate for a configured demo domain, executes its renewal, and shows the
  merchant's completed checkout result end to end. A missing or insufficient
  mandate must downgrade to review without attempting payment.
- Phase 4 done when: the full flow can be demoed live from a cold dashboard
  load without any manual database seeding happening on camera, README is
  complete with the disclosure section, and every test in the repo passes
