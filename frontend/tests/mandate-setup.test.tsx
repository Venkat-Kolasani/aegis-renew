import assert from "node:assert/strict";
import { test } from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import MandateSetup, {
  buildMandateRequestBody,
  type MandateDomainOption,
} from "../components/MandateSetup";

const domains: MandateDomainOption[] = [
  { id: 2, domain: "billing.aegis-demo.test" },
  { id: 1, domain: "docs.aegis-demo.test" },
];

test("renders idle mandate form with DEMO merchant defaults", () => {
  const markup = renderToStaticMarkup(<MandateSetup domains={domains} />);
  assert.match(markup, /Yearly renewal mandate/);
  assert.match(markup, /Aegis Demo Registrar/);
  assert.match(markup, /https:\/\/example\.com/);
  assert.match(markup, /Fixed DEMO mandate coverage/);
  assert.match(markup, /yearly/);
  assert.doesNotMatch(markup, /<input/);
  assert.match(markup, /data-state="idle"/);
  assert.match(markup, /billing\.aegis-demo\.test/);
});

test("renders empty-domain error state", () => {
  const markup = renderToStaticMarkup(<MandateSetup domains={[]} />);
  assert.match(markup, /No domains available for mandate setup/);
  assert.match(markup, /data-state="error"/);
});

test("renders mocked awaiting-approval success path", () => {
  const markup = renderToStaticMarkup(
    <MandateSetup
      domains={domains}
      initialState="awaiting_approval"
      initialApprovalUrl="https://sandbox.collect.prava.space/?session=ses_mock"
    />,
  );
  assert.match(markup, /data-state="awaiting_approval"/);
  assert.match(markup, /Approve with your passkey/);
  assert.match(markup, /I approved it—sync mandate/);
  assert.match(markup, /Reopen approval/);
  assert.match(markup, /Cancel/);
  assert.doesNotMatch(markup, /456789/);
});

test("renders sanitized active reconciliation guidance", () => {
  const markup = renderToStaticMarkup(
    <MandateSetup domains={domains} initialState="active" />,
  );
  assert.match(markup, /data-state="active"/);
  assert.match(markup, /Active mandate synced/);
  assert.match(markup, /OpenAI ranking starts automatically/);
  assert.match(markup, /auto_renew/);
});

test("renders mocked cancellation state", () => {
  const markup = renderToStaticMarkup(
    <MandateSetup domains={domains} initialState="cancelled" />,
  );
  assert.match(markup, /data-state="cancelled"/);
  assert.match(markup, /Mandate approval cancelled/);
  assert.match(markup, /Try again/);
});

test("renders mocked provider failure state", () => {
  const markup = renderToStaticMarkup(
    <MandateSetup
      domains={domains}
      initialState="error"
      initialErrorMessage="Prava mandate session failed (HTTP 401)"
    />,
  );
  assert.match(markup, /data-state="error"/);
  assert.match(markup, /Prava mandate session failed \(HTTP 401\)/);
});

test("renders mocked expired state", () => {
  const markup = renderToStaticMarkup(
    <MandateSetup
      domains={domains}
      initialState="expired"
      initialErrorMessage="The Prava approval session expired. Start again to mint a fresh session."
    />,
  );
  assert.match(markup, /data-state="expired"/);
  assert.match(markup, /The Prava approval session expired/);
  assert.match(markup, /Try again/);
});

test("buildMandateRequestBody locks yearly frequency and normalizes codes", () => {
  assert.deepEqual(
    buildMandateRequestBody({
      domainId: 2,
      merchantName: " Aegis Demo Registrar ",
      merchantUrl: "https://example.com",
      merchantCountry: "us",
      capAmount: 18,
      currency: "usd",
    }),
    {
      domain_id: 2,
      merchant_name: "Aegis Demo Registrar",
      merchant_url: "https://example.com",
      merchant_country: "US",
      cap_amount: 18,
      currency: "USD",
      frequency: "yearly",
    },
  );
});
