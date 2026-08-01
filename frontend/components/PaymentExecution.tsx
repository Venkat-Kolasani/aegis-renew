"use client";

import { FormEvent, useMemo, useState } from "react";

import {
  executePayment,
  type PaymentExecutionResult,
} from "@/lib/aegisApi";

export type PaymentDomainOption = {
  id: number;
  domain: string;
};

export type PaymentExecutionProps = {
  domains: PaymentDomainOption[];
  apiBaseUrl?: string;
  initialResult?: PaymentExecutionResult | null;
  initialLoading?: boolean;
  initialError?: string | null;
};

function resultMessage(result: PaymentExecutionResult): string {
  if (result.completed && result.payment_status === "completed") {
    return "Merchant checkout completed and Prava confirmed the outcome.";
  }
  if (result.completed) {
    return "Merchant checkout completed, but provider reporting needs reconciliation.";
  }
  return "The renewal checkout did not complete.";
}

export default function PaymentExecution({
  domains,
  apiBaseUrl,
  initialResult = null,
  initialLoading = false,
  initialError = null,
}: PaymentExecutionProps) {
  const sortedDomains = useMemo(
    () => [...domains].sort((left, right) => left.domain.localeCompare(right.domain)),
    [domains],
  );
  const [domainId, setDomainId] = useState<number | null>(sortedDomains[0]?.id ?? null);
  const [result, setResult] = useState<PaymentExecutionResult | null>(initialResult);
  const [loading, setLoading] = useState(initialLoading);
  const [error, setError] = useState<string | null>(initialError);

  async function onExecute(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (loading) return;
    if (domainId === null) {
      setError("Scan a domain before executing a renewal.");
      return;
    }
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      setResult(await executePayment(domainId, apiBaseUrl));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Renewal execution failed");
    } finally {
      setLoading(false);
    }
  }

  const state = loading
    ? "loading"
    : error
      ? "error"
      : result
        ? result.payment_status === "completed"
          ? "completed"
          : result.completed
            ? "reconciliation"
            : "failed"
        : "idle";

  return (
    <section
      aria-labelledby="payment-execution-heading"
      className="aegis-panel space-y-5 rounded-xl p-6 sm:p-7"
      data-state={state}
    >
      <div className="space-y-2 border-b border-line pb-5">
        <h2 id="payment-execution-heading" className="text-base font-semibold text-ink">
          Covered renewal execution
        </h2>
        <p className="max-w-2xl text-sm leading-relaxed text-ink-muted">
          The server reloads the latest decision, quote, and active mandate before charging.
        </p>
        <p className="rounded-md border border-line bg-neutral-soft px-3 py-2 text-xs text-ink-muted">
          The browser sends only a domain id—never an amount, mandate id, merchant, or payment
          credential.
        </p>
      </div>

      <form className="flex flex-col gap-3 sm:flex-row sm:items-end" onSubmit={onExecute}>
        <label className="block flex-1 space-y-1.5 text-sm text-ink">
          <span className="font-medium">Domain</span>
          <select
            className="aegis-input font-mono"
            value={domainId ?? ""}
            onChange={(event) => {
              const next = Number(event.target.value);
              setDomainId(Number.isInteger(next) && next > 0 ? next : null);
            }}
            disabled={loading || sortedDomains.length === 0}
          >
            {sortedDomains.map((domain) => (
              <option key={domain.id} value={domain.id}>
                {domain.domain}
              </option>
            ))}
          </select>
        </label>
        <button
          type="submit"
          className="aegis-btn aegis-btn-primary"
          disabled={loading || domainId === null}
        >
          {loading ? "Executing…" : "Execute covered renewal"}
        </button>
      </form>

      {sortedDomains.length === 0 && !error ? (
        <p className="text-sm text-ink-muted">Scan a domain before executing a renewal.</p>
      ) : null}
      {error ? (
        <p
          role="alert"
          className="rounded-lg border border-danger/20 bg-danger-soft px-3 py-2.5 text-sm text-danger"
        >
          {error}
        </p>
      ) : null}
      {loading ? (
        <p role="status" className="text-sm text-ink-muted">
          Rechecking current mandate coverage and merchant quote…
        </p>
      ) : null}
      {!loading && !error && result ? (
        <div
          role="status"
          className={`rounded-lg border px-4 py-3 text-sm ${
            result.payment_status === "completed"
              ? "border-ok/25 bg-ok-soft text-ok"
              : result.completed
                ? "border-warn/25 bg-warn-soft text-warn"
                : "border-danger/20 bg-danger-soft text-danger"
          }`}
        >
          <p className="font-medium">{resultMessage(result)}</p>
          <dl className="mt-3 grid gap-2 font-mono text-xs sm:grid-cols-3">
            <div>
              <dt className="text-ink-faint">payment_status</dt>
              <dd className="mt-0.5 break-words">{result.payment_status}</dd>
            </div>
            <div>
              <dt className="text-ink-faint">merchant_order_ref</dt>
              <dd className="mt-0.5 break-words">{result.merchant_order_ref ?? "—"}</dd>
            </div>
            <div>
              <dt className="text-ink-faint">completed</dt>
              <dd className="mt-0.5">{String(result.completed)}</dd>
            </div>
          </dl>
        </div>
      ) : null}
    </section>
  );
}
