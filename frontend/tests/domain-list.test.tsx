import assert from "node:assert/strict";
import { test } from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import DomainList, { type DomainSummary } from "../components/DomainList";

const domain: DomainSummary = {
  id: 1,
  domain: "api.example.com",
  expiry_date: "2027-03-15",
  cert_expiry_date: "2026-12-01",
  dns_risk: false,
  last_scanned: "2026-08-01T03:00:00Z",
};

const today = new Date("2026-08-01T00:00:00Z");

test("renders the empty state", () => {
  assert.match(renderToStaticMarkup(<DomainList domains={[]} />), /No domains scanned yet/);
});

test("renders the loading state", () => {
  assert.match(renderToStaticMarkup(<DomainList domains={[]} loading />), /Loading domains/);
});

test("renders the error-ready state", () => {
  const markup = renderToStaticMarkup(<DomainList domains={[]} error="Scanner unavailable" />);
  assert.match(markup, /Domain inventory unavailable/);
  assert.match(markup, /Scanner unavailable/);
});

test("renders a healthy populated domain", () => {
  const markup = renderToStaticMarkup(<DomainList domains={[domain]} today={today} />);
  assert.match(markup, /api\.example\.com/);
  assert.match(markup, /Healthy/);
  assert.match(markup, /DNS clear/);
  assert.match(markup, /2027-03-15/);
  assert.match(markup, /2026-08-01 03:00 UTC/);
});

test("renders a near-expiry warning", () => {
  const markup = renderToStaticMarkup(
    <DomainList
      domains={[{ ...domain, expiry_date: "2026-08-20", cert_expiry_date: "2026-11-01" }]}
      today={today}
    />,
  );
  assert.match(markup, /Review soon/);
  assert.match(markup, /19d remaining/);
});

test("renders a certificate urgency state", () => {
  const markup = renderToStaticMarkup(
    <DomainList domains={[{ ...domain, cert_expiry_date: "2026-08-04" }]} today={today} />,
  );
  assert.match(markup, /Urgent/);
  assert.match(markup, /2026-08-04/);
});

test("renders DNS takeover risk separately from expiry urgency", () => {
  const markup = renderToStaticMarkup(
    <DomainList domains={[{ ...domain, dns_risk: true }]} today={today} />,
  );
  assert.match(markup, /DNS takeover risk/);
  assert.match(markup, /data-risk="green"/);
  assert.match(markup, /bg-dns-soft/);
});
