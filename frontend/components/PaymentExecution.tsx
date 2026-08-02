"use client";

import { FormEvent, useMemo, useState } from "react";

import {
  executePayment,
  type PaymentExecutionResult,
  type RankDecision,
} from "@/lib/aegisApi";

export type PaymentDomainOption = {
  id: number;
  domain: string;
};

export type PaymentExecutionProps = {
  domains: PaymentDomainOption[];
  apiBaseUrl?: string;
  selectedDomainId?: number | null;
  onSelectedDomainIdChange?: (domainId: number | null) => void;
  mandateActiveForSelected?: boolean;
  latestDecision?: RankDecision | null;
  initialResult?: PaymentExecutionResult | null;
  initialLoading?: boolean;
  initialError?: string | null;
};

function resultMessage(result: PaymentExecutionResult): string {
  if (result.completed && result.payment_status === "completed") {
    return "Autonomous renewal finished: merchant checkout completed and Prava confirmed the outcome.";
  }
  if (result.completed) {
    return "Merchant checkout completed, but provider reporting needs reconciliation.";
  }
  return "The renewal checkout did not complete.";
}

function readinessCopy(input: {
  hasDomains: boolean;
  mandateActive: boolean;
  decision: RankDecision | null | undefined;
}): { ready: boolean; message: string } {
  if (!input.hasDomains) {
    return { ready: false, message: "Scan a domain before executing a renewal." };
  }
  if (!input.mandateActive) {
    return {
      ready: false,
      message:
        "Approve and sync a yearly Prava mandate for this domain first. Autonomy requires standing coverage.",
    };
  }
  if (!input.decision) {
    return {
      ready: false,
      message:
        "Run OpenAI ranking for this inventory. Execution only proceeds on a final auto_renew decision.",
    };
  }
  if (input.decision.decision !== "auto_renew") {
    return {
      ready: false,
      message: `OpenAI + policy returned “${input.decision.decision.replaceAll("_", " ")}” — autonomous charge is blocked until the decision is auto_renew.`,
    };
  }
  return {
    ready: true,
    message:
      "Ready: active mandate + final auto_renew. Browser still sends only domain_id; the server charges under the cap.",
  };
}

export default function PaymentExecution({
  domains,
  apiBaseUrl,
  selectedDomainId,
  onSelectedDomainIdChange,
  mandateActiveForSelected = false,
  latestDecision = null,
  initialResult = null,
  initialLoading = false,
  initialError = null,
}: PaymentExecutionProps) {
  const sortedDomains = useMemo(
    () => [...domains].sort((left, right) => left.domain.localeCompare(right.domain)),
    [domains],
  );
  const controlled = selectedDomainId !== undefined;
  const [internalDomainId, setInternalDomainId] = useState<number | null>(
    sortedDomains[0]?.id ?? null,
  );
  const domainId = useMemo(() => {
    if (controlled) return selectedDomainId ?? null;
    if (
      internalDomainId !== null &&
      sortedDomains.some((domain) => domain.id === internalDomainId)
    ) {
      return internalDomainId;
    }
    return sortedDomains[0]?.id ?? null;
  }, [controlled, selectedDomainId, internalDomainId, sortedDomains]);
  const [result, setResult] = useState<PaymentExecutionResult | null>(initialResult);
  const [loading, setLoading] = useState(initialLoading);
  const [error, setError] = useState<string | null>(initialError);

  function setDomainId(next: number | null): void {
    if (controlled) {
      onSelectedDomainIdChange?.(next);
      return;
    }
    setInternalDomainId(next);
  }

  const readiness = readinessCopy({
    hasDomains: sortedDomains.length > 0,
    mandateActive: mandateActiveForSelected,
    decision: latestDecision,
  });

  async function onExecute(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (loading) return;
    if (domainId === null) {
      setError("Scan a domain before executing a renewal.");
      return;
    }
    if (!readiness.ready) {
      setError(readiness.message);
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
      data-ready={readiness.ready ? "true" : "false"}
    >
      <div className="space-y-2 border-b border-line pb-5">
        <p className="font-mono text-[10px] font-semibold uppercase tracking-[0.16em] text-ink-faint">
          Step 4 · Autonomous renew
        </p>
        <h2 id="payment-execution-heading" className="mt-1 text-base font-semibold text-ink">
          Covered renewal execution
        </h2>
        <p className="max-w-2xl text-sm leading-relaxed text-ink-muted">
          After you approve a mandate once, Aegis can renew without another passkey when OpenAI +
          policy say <code className="font-mono text-[11px]">auto_renew</code> and the quote fits
          the cap.
        </p>
        <p className="rounded-md border border-line bg-neutral-soft px-3 py-2 text-xs text-ink-muted">
          The browser sends only a domain id—never an amount, mandate id, merchant, or payment
          credential.
        </p>
      </div>

      <p
        role="status"
        className={`rounded-lg border px-3 py-2.5 text-sm ${
          readiness.ready
            ? "border-ok/25 bg-ok-soft text-ok"
            : "border-line bg-neutral-soft text-ink-muted"
        }`}
      >
        {readiness.message}
      </p>

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
          className={`aegis-btn min-w-[12rem] ${
            readiness.ready ? "aegis-btn-accent" : "aegis-btn-secondary"
          }`}
          disabled={loading || domainId === null || !readiness.ready}
          title={
            readiness.ready
              ? "Server will recheck mandate, quote, and auto_renew before charging"
              : readiness.message
          }
        >
          {loading
            ? "Executing…"
            : readiness.ready
              ? "Execute autonomous renewal"
              : "Waiting for auto_renew"}
        </button>
      </form>

      {!readiness.ready && domainId !== null ? (
        <p className="text-xs text-ink-muted">
          Safety gate is working: execute stays disabled until OpenAI + policy return{" "}
          <code className="font-mono text-[11px]">auto_renew</code> for a mandated domain.
          Nearer-expiry hosts (or the DEMO host after a chargeable ranking) unlock the button.
        </p>
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
          Rechecking OpenAI decision, mandate coverage, and merchant quote…
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
