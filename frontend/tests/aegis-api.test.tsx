import assert from "node:assert/strict";
import { test } from "node:test";

import {
  isValidScanDomain,
  executePayment,
  parseDomainSummary,
  parsePaymentExecutionResult,
  parseRankDecision,
  parseScanResult,
  reconcileMandate,
  rankDomains,
  resolveApiBase,
  type DomainSummary,
} from "../lib/aegisApi";
import { summarizeDomains } from "../lib/domainSummary";

test("resolveApiBase defaults to the Next rewrite prefix", () => {
  assert.equal(resolveApiBase(), "/aegis-api");
  assert.equal(resolveApiBase("http://localhost:8000/"), "http://localhost:8000");
});

test("isValidScanDomain accepts bare hostnames only", () => {
  assert.equal(isValidScanDomain("example.com"), true);
  assert.equal(isValidScanDomain("docs.example.com."), true);
  assert.equal(isValidScanDomain("https://example.com"), false);
  assert.equal(isValidScanDomain("example"), false);
  assert.equal(isValidScanDomain("192.168.0.1"), false);
});

test("parseDomainSummary keeps contract fields without remapping", () => {
  const parsed = parseDomainSummary({
    id: 2,
    domain: "billing.example.com",
    expiry_date: "2026-08-20",
    cert_expiry_date: null,
    dns_risk: false,
    last_scanned: "2026-08-01T12:00:00Z",
  });
  assert.deepEqual(parsed, {
    id: 2,
    domain: "billing.example.com",
    expiry_date: "2026-08-20",
    cert_expiry_date: null,
    dns_risk: false,
    last_scanned: "2026-08-01T12:00:00Z",
  });
});

test("parseDomainSummary accepts null last_scanned", () => {
  const parsed = parseDomainSummary({
    id: 4,
    domain: "partial.example.com",
    expiry_date: null,
    cert_expiry_date: null,
    dns_risk: false,
    last_scanned: null,
  });
  assert.equal(parsed?.last_scanned, null);
});

test("parseScanResult accepts dns_risk_detail including null", () => {
  const parsed = parseScanResult({
    id: 3,
    domain: "cdn.example.com",
    expiry_date: null,
    cert_expiry_date: "2026-09-01T00:00:00Z",
    dns_risk: true,
    dns_risk_detail: "high confidence dangling CNAME",
  });
  assert.equal(parsed?.dns_risk, true);
  assert.equal(parsed?.dns_risk_detail, "high confidence dangling CNAME");
  assert.equal(parsed?.expiry_date, null);
});

test("parseRankDecision accepts contract decisions and rejects invalid rows", () => {
  const parsed = parseRankDecision({
    domain_id: 7,
    criticality_score: 88,
    decision: "auto_renew",
    reason: "Expiry is near.",
  });
  assert.deepEqual(parsed, {
    domain_id: 7,
    criticality_score: 88,
    decision: "auto_renew",
    reason: "Expiry is near.",
  });
  assert.equal(
    parseRankDecision({
      domain_id: 7,
      criticality_score: 101,
      decision: "auto_renew",
      reason: "bad",
    }),
    null,
  );
  assert.equal(
    parseRankDecision({
      domain_id: 0,
      criticality_score: 40,
      decision: "ignore",
      reason: "ok",
    }),
    null,
  );
  assert.equal(
    parseRankDecision({
      domain_id: 7,
      criticality_score: 40,
      decision: "renew_now",
      reason: "ok",
    }),
    null,
  );
  assert.equal(
    parseRankDecision({
      domain_id: 7,
      criticality_score: 40,
      decision: "ignore",
      reason: "   ",
    }),
    null,
  );
});

test("parsePaymentExecutionResult accepts only the locked response fields", () => {
  assert.deepEqual(
    parsePaymentExecutionResult({
      payment_status: "reconciliation_required",
      merchant_order_ref: "DEMO-REN-20260802-ABC12345",
      completed: true,
    }),
    {
      payment_status: "reconciliation_required",
      merchant_order_ref: "DEMO-REN-20260802-ABC12345",
      completed: true,
    },
  );
  assert.equal(
    parsePaymentExecutionResult({
      payment_status: "completed",
      merchant_order_ref: null,
      completed: "true",
    }),
    null,
  );
});

test("executePayment sends only domain_id and parses the locked result", async () => {
  const originalFetch = globalThis.fetch;
  let requestBody = "";
  globalThis.fetch = (async (_input, init) => {
    requestBody = String(init?.body);
    return {
      ok: true,
      json: async () => ({
        payment_status: "completed",
        merchant_order_ref: "DEMO-REN-20260802-ABC12345",
        completed: true,
      }),
    } as Response;
  }) as typeof fetch;
  try {
    const result = await executePayment(7, "http://example.test");
    assert.deepEqual(JSON.parse(requestBody), { domain_id: 7 });
    assert.equal(result.payment_status, "completed");
    assert.equal(result.completed, true);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("reconcileMandate sends only domain_id and accepts active status", async () => {
  const originalFetch = globalThis.fetch;
  let requestBody = "";
  globalThis.fetch = (async (_input, init) => {
    requestBody = String(init?.body);
    return {
      ok: true,
      json: async () => ({ status: "active" }),
    } as Response;
  }) as typeof fetch;
  try {
    const result = await reconcileMandate(7, "http://example.test");
    assert.deepEqual(JSON.parse(requestBody), { domain_id: 7 });
    assert.equal(result.status, "active");
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("summarizeDomains counts urgency and DNS risk", () => {
  const today = new Date("2026-08-01T00:00:00Z");
  const domains: DomainSummary[] = [
    {
      id: 1,
      domain: "a.example.com",
      expiry_date: "2026-08-04",
      cert_expiry_date: null,
      dns_risk: false,
      last_scanned: "2026-08-01T00:00:00Z",
    },
    {
      id: 2,
      domain: "b.example.com",
      expiry_date: "2026-08-20",
      cert_expiry_date: "2027-01-01",
      dns_risk: true,
      last_scanned: "2026-08-01T00:00:00Z",
    },
  ];
  assert.deepEqual(summarizeDomains(domains, today), {
    monitored: 2,
    urgent: 1,
    reviewSoon: 1,
    dnsRisk: 1,
  });
});

test("rankDomains normalizes timeout and malformed JSON errors", async () => {
  const originalFetch = globalThis.fetch;

  globalThis.fetch = (async () => {
    const err = new Error("The operation was aborted due to timeout");
    err.name = "TimeoutError";
    throw err;
  }) as typeof fetch;
  await assert.rejects(
    () => rankDomains([1], "http://example.test"),
    (err: unknown) =>
      err instanceof Error && err.message === "Ranking timed out. Try again.",
  );

  globalThis.fetch = (async () =>
    ({
      ok: true,
      json: async () => {
        throw new SyntaxError("Unexpected token");
      },
    }) as Response) as typeof fetch;
  await assert.rejects(
    () => rankDomains([1], "http://example.test"),
    (err: unknown) =>
      err instanceof Error && err.message === "Rank response was malformed",
  );

  globalThis.fetch = originalFetch;
});
