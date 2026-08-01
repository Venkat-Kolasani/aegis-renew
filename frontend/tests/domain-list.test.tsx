import assert from "node:assert/strict";
import { test } from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import DomainList, { type DomainSummary } from "../components/DomainList";

const domain: DomainSummary = {
  id: 1,
  domain: "api.example.com",
  expiry_date: "2026-08-24",
  cert_expiry_date: "2026-08-08",
  dns_risk: false,
  last_scanned: "2026-08-01T03:00:00Z",
};

test("renders the empty state", () => {
  assert.match(renderToStaticMarkup(<DomainList domains={[]} />), /No domains scanned yet/);
});

test("renders the loading state", () => {
  assert.match(renderToStaticMarkup(<DomainList domains={[]} loading />), /Loading domains/);
});

test("renders a populated domain and risk badge", () => {
  const markup = renderToStaticMarkup(<DomainList domains={[{ ...domain, dns_risk: true }]} />);
  assert.match(markup, /api\.example\.com/);
  assert.match(markup, /DNS risk/);
  assert.match(markup, /2026-08-24/);
});
