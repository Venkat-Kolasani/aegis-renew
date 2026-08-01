# Aegis - Autonomous Infrastructure Renewal Agent

Agentic Commerce Hackathon (Prava), August 1-3 2026, two-person team.

## Product

Aegis monitors a small portfolio of domains for expiry, TLS expiry, and
dangling-DNS takeover risk. It uses those facts to rank urgency, then renews a
configured domain only when a user-approved, merchant-locked Prava mandate
covers the exact action.

The paid v1 action is a **domain renewal**. TLS and DNS signals make a domain
more urgent; they are not independent purchases in the hackathon demo.

## Non-negotiable hackathon facts

- Kickoff: July 31, 7:00 PM PT / August 1, 7:30 AM IST.
- Hard submission deadline: August 2, 3:00 PM PT / August 3, 3:30 AM IST.
- A visible completed merchant checkout is required. A session, approval URL,
  or payment credential is not proof of a completed transaction.
- All judged application code is written during the build window. README must
  disclose that the prior work was only the idea and research.
- Never commit or display API keys, card data, network tokens, dynamic CVVs, or
  raw Prava mandate identifiers.

## Phase 0 gate - prove commerce first

Before building application payment routes, Venkat must:

1. Read the official `prava-sdk-integration` and `prava-pay` skills.
2. Confirm Prava sandbox health, credentials, a supported browser, and test
   card access.
3. Create and activate a merchant-locked yearly mandate through the official
   supported path; retain only sanitized evidence.
4. Timebox merchant discovery to one hour: Prava Merchant List, UCP Checker,
   Composio MCP Gateway, and e-commerce MCP directories. Verify checkout with
   the chosen merchant rather than treating a directory entry as proof.
5. Complete one real sandbox checkout using the mandate. If a self-built
   merchant is needed, first prove that its processor accepts the Prava
   credential, then mark every involved file and the README with `DEMO`.

If native mandate proof is unavailable, Aegis changes its product claim to
"agent-prepared renewal with a passkey per purchase." It does not fake
autonomous payment.

## Architecture and safety boundary

```text
Live RDAP / crt.sh / DNS -> FastAPI scan -> Postgres -> OpenAI ranking
                                                    -> deterministic policy
active merchant-locked mandate + current quote ----> payment executor
Prava mandate charge -> merchant checkout -> report outcome -> audit log/UI
```

- `POST /api/agent/rank` records an LLM recommendation only; it cannot spend.
- A deterministic policy checks the selected domain, active mandate, merchant
  binding, exact current price, and cap before it calls `on_agent_decision`.
- `POST /api/payments/execute` accepts only `domain_id`; the server resolves
  the mandate and price. Browser input never selects a mandate or amount.
- Store sanitized transaction state and merchant order reference only.

## Work split

| Owner | Responsibility |
|---|---|
| Vivek | FastAPI app registration, schema, RDAP, crt.sh, DNS risk, ranking, deterministic policy |
| Venkat | Next.js UI, Prava mandate/payment routes, merchant proof, payment evidence |
| Both | Phase gates, contract review, README, demo, deployment |

Use separate branches/checkouts. No owner edits another owner's file. Schema
changes require an explicit handoff before editing.

## Commit and checkpoint rhythm

Every prompt produces one small, validated commit and a matching README update
when behavior, configuration, API, or a manual test changes. Commit prefixes:
`[docs]`, `[backend]`, `[frontend]`, `[payments]`, `[infra]`, `[ci]`.

1. Phase 0: documentation, scaffold, CI, and the commerce-proof gate.
2. Phase 1: independent detection and dashboard slices; each has unit tests.
3. Phase 2: ranking and policy; rank endpoint remains side-effect-free.
4. Phase 3: native mandate charge to verified merchant checkout; manual
   sandbox smoke test plus sanitized evidence.
5. Phase 4: cold-start demo, README disclosure, deployment, video, submission.

## Target tracks

Prioritize Prava Overall, OpenAI, Visa Intelligent Commerce, and Localhost.
Do not add Linq, NANDA, or Senso unless the complete core transaction and demo
are already passing.

## Demo story

1. Load Aegis and scan real public domain data.
2. Show TLS/DNS context and an explainable ranking decision.
3. Show the previously approved merchant-locked yearly mandate and its cap.
4. Trigger the deterministic, covered renewal path.
5. Show the merchant's completed checkout result and Aegis audit entry.
6. State exactly what is live, what is demo-only, and what was built during the
   event.
