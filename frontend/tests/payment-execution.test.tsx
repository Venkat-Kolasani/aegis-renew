import assert from "node:assert/strict";
import { test } from "node:test";
import { renderToStaticMarkup } from "react-dom/server";

import PaymentExecution from "../components/PaymentExecution";

const domains = [{ id: 7, domain: "billing.aegis-demo.test" }];

test("renders an idle execution control with no credential inputs", () => {
  const markup = renderToStaticMarkup(<PaymentExecution domains={domains} />);
  assert.match(markup, /data-state="idle"/);
  assert.match(markup, /Execute covered renewal/);
  assert.match(markup, /browser sends only a domain id/i);
  assert.doesNotMatch(markup, /name="(?:amount|mandate|token|cvv|expiry|currency)"/i);
});

test("renders completed merchant result fields", () => {
  const markup = renderToStaticMarkup(
    <PaymentExecution
      domains={domains}
      initialResult={{
        payment_status: "completed",
        merchant_order_ref: "DEMO-REN-20260802-ABC12345",
        completed: true,
      }}
    />,
  );
  assert.match(markup, /data-state="completed"/);
  assert.match(markup, /payment_status/);
  assert.match(markup, /DEMO-REN-20260802-ABC12345/);
  assert.match(markup, /completed/);
  assert.match(markup, />true</);
});

test("renders report failure as reconciliation rather than full success", () => {
  const markup = renderToStaticMarkup(
    <PaymentExecution
      domains={domains}
      initialResult={{
        payment_status: "reconciliation_required",
        merchant_order_ref: "DEMO-REN-20260802-ABC12345",
        completed: true,
      }}
    />,
  );
  assert.match(markup, /data-state="reconciliation"/);
  assert.match(markup, /needs reconciliation/i);
  assert.doesNotMatch(markup, /Prava confirmed the outcome/);
});

test("renders non-completed and API failure states clearly", () => {
  const declined = renderToStaticMarkup(
    <PaymentExecution
      domains={domains}
      initialResult={{
        payment_status: "declined",
        merchant_order_ref: null,
        completed: false,
      }}
    />,
  );
  assert.match(declined, /data-state="failed"/);
  assert.match(declined, /did not complete/i);

  const error = renderToStaticMarkup(
    <PaymentExecution domains={domains} initialError="Prava mandate charge failed" />,
  );
  assert.match(error, /data-state="error"/);
  assert.match(error, /Prava mandate charge failed/);
});
