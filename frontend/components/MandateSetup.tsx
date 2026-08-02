"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";

import { reconcileMandate } from "@/lib/aegisApi";

export type MandateDomainOption = {
  id: number;
  domain: string;
};

export type MandateUiState =
  | "idle"
  | "loading"
  | "awaiting_approval"
  | "syncing"
  | "active"
  | "cancelled"
  | "error"
  | "expired";

export type MandateSetupProps = {
  domains: MandateDomainOption[];
  apiBaseUrl?: string;
  selectedDomainId?: number | null;
  onSelectedDomainIdChange?: (domainId: number | null) => void;
  onMandateCoverageChange?: (domainId: number | null, active: boolean) => void;
  /** DEMO defaults: Aegis Demo Registrar (checkout via /api/demo/registrar/*). */
  defaultMerchantName?: string;
  defaultMerchantUrl?: string;
  defaultMerchantCountry?: string;
  defaultCapAmount?: number;
  defaultCurrency?: string;
  openApprovalUrl?: (url: string) => void;
  /** Test/demo seam for rendering non-idle states without a live Prava session. */
  initialState?: MandateUiState;
  initialErrorMessage?: string | null;
  initialApprovalUrl?: string | null;
};

export function buildMandateRequestBody(input: {
  domainId: number;
  merchantName: string;
  merchantUrl: string;
  merchantCountry: string;
  capAmount: number;
  currency: string;
}): Record<string, string | number> {
  return {
    domain_id: input.domainId,
    merchant_name: input.merchantName.trim(),
    merchant_url: input.merchantUrl.trim(),
    merchant_country: input.merchantCountry.trim().toUpperCase(),
    cap_amount: input.capAmount,
    currency: input.currency.trim().toUpperCase(),
    frequency: "yearly",
  };
}

export function isSafeHttpsUrl(value: string): boolean {
  try {
    const parsed = new URL(value);
    return parsed.protocol === "https:";
  } catch {
    return false;
  }
}

type MandateApiResponse = {
  status: string;
  approval_url: string;
};

function resolveApiBase(apiBaseUrl?: string): string {
  // Same-origin Next rewrite avoids browser CORS and keeps secrets on the API.
  if (apiBaseUrl && apiBaseUrl.length > 0) return apiBaseUrl.replace(/\/$/, "");
  return "/aegis-api";
}

function initialDomainId(domains: MandateDomainOption[]): number | null {
  const first = domains[0];
  return first && first.id > 0 ? first.id : null;
}

export default function MandateSetup({
  domains,
  apiBaseUrl,
  selectedDomainId: controlledDomainId,
  onSelectedDomainIdChange,
  onMandateCoverageChange,
  // DEMO: JOINT-2/VENKAT-3 self-owned registrar; mandate URL stays example.com.
  defaultMerchantName = "Aegis Demo Registrar",
  defaultMerchantUrl = "https://example.com",
  defaultMerchantCountry = "US",
  defaultCapAmount = 18,
  defaultCurrency = "USD",
  openApprovalUrl = (url: string) => {
    if (typeof window !== "undefined") {
      window.open(url, "_blank", "noopener,noreferrer");
    }
  },
  initialState,
  initialErrorMessage = null,
  initialApprovalUrl = null,
}: MandateSetupProps) {
  const sortedDomains = useMemo(
    () => [...domains].sort((left, right) => left.domain.localeCompare(right.domain)),
    [domains],
  );

  const controlled = controlledDomainId !== undefined;
  const [internalDomainId, setInternalDomainId] = useState<number | null>(() =>
    initialDomainId(sortedDomains),
  );
  const domainId = controlled ? (controlledDomainId ?? null) : internalDomainId;
  const [state, setState] = useState<MandateUiState>(
    initialState ?? (sortedDomains.length ? "idle" : "error"),
  );
  const [errorMessage, setErrorMessage] = useState<string | null>(
    initialErrorMessage ??
      (sortedDomains.length ? null : "No domains available for mandate setup."),
  );
  const [approvalUrl, setApprovalUrl] = useState<string | null>(initialApprovalUrl);

  const selectedDomainId = useMemo(() => {
    if (sortedDomains.length === 0) return null;
    if (domainId !== null && sortedDomains.some((domain) => domain.id === domainId)) {
      return domainId;
    }
    return initialDomainId(sortedDomains);
  }, [sortedDomains, domainId]);

  useEffect(() => {
    onMandateCoverageChange?.(
      selectedDomainId,
      state === "active" ? true : false,
    );
  }, [onMandateCoverageChange, selectedDomainId, state]);

  function setDomainId(nextId: number | null): void {
    if (controlled) {
      onSelectedDomainIdChange?.(nextId);
      return;
    }
    setInternalDomainId(nextId);
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (state === "loading") return;

    setState("loading");
    setErrorMessage(null);
    setApprovalUrl(null);

    if (selectedDomainId === null || selectedDomainId <= 0) {
      setState("error");
      setErrorMessage("Select a valid domain before starting mandate approval.");
      return;
    }

    try {
      const response = await fetch(`${resolveApiBase(apiBaseUrl)}/payments/mandate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(
          buildMandateRequestBody({
            domainId: selectedDomainId,
            merchantName: defaultMerchantName,
            merchantUrl: defaultMerchantUrl,
            merchantCountry: defaultMerchantCountry,
            capAmount: defaultCapAmount,
            currency: defaultCurrency,
          }),
        ),
      });

      const payload = (await response.json().catch(() => null)) as
        | MandateApiResponse
        | { detail?: string }
        | null;

      if (!response.ok) {
        const detail =
          payload && "detail" in payload && typeof payload.detail === "string"
            ? payload.detail
            : `Mandate setup failed (HTTP ${response.status})`;
        setState("error");
        setErrorMessage(detail);
        return;
      }

      if (!payload || !("approval_url" in payload) || typeof payload.approval_url !== "string") {
        setState("error");
        setErrorMessage("Mandate setup returned an invalid approval URL.");
        return;
      }

      if (!isSafeHttpsUrl(payload.approval_url)) {
        setState("error");
        setErrorMessage("Mandate setup returned an invalid approval URL.");
        return;
      }

      setApprovalUrl(payload.approval_url);
      setState("awaiting_approval");
      openApprovalUrl(payload.approval_url);
    } catch {
      setState("error");
      setErrorMessage("Could not reach the Aegis payment API.");
    }
  }

  function onCancel(): void {
    setState("cancelled");
    setErrorMessage(null);
  }

  async function onReconcile(): Promise<void> {
    if (selectedDomainId === null || state === "syncing") return;
    setState("syncing");
    setErrorMessage(null);
    try {
      await reconcileMandate(selectedDomainId, apiBaseUrl);
      setState("active");
    } catch (error) {
      setState("error");
      setErrorMessage(
        error instanceof Error ? error.message : "Mandate reconciliation failed",
      );
    }
  }

  function onMarkExpired(): void {
    setState("expired");
    setErrorMessage("The Prava approval session expired. Start again to mint a fresh session.");
  }

  function onReset(): void {
    setState("idle");
    setErrorMessage(null);
    setApprovalUrl(null);
  }

  return (
    <section
      aria-labelledby="mandate-setup-heading"
      className="aegis-panel space-y-5 rounded-xl p-6 sm:p-7"
      data-state={state}
    >
      <div className="space-y-2 border-b border-line pb-5">
        <p className="font-mono text-[10px] font-semibold uppercase tracking-[0.16em] text-ink-faint">
          Step 2 · Standing authority
        </p>
        <h2 id="mandate-setup-heading" className="mt-1 text-base font-semibold text-ink">
          Yearly renewal mandate
        </h2>
        <p className="max-w-2xl text-sm leading-relaxed text-ink-muted">
          Approve once with a passkey. That standing Prava mandate is what makes later renewals
          autonomous—no card in chat, no per-renewal biometric when coverage still matches.
        </p>
        <p className="rounded-md border border-warn/20 bg-warn-soft px-3 py-2 text-xs text-warn">
          {/* DEMO: JOINT-2 / VENKAT-3 merchant path */}
          DEMO merchant: Aegis Demo Registrar ($18/year). Checkout completes via the Aegis DEMO
          adapter, not a live registrar storefront.
        </p>
      </div>

      <form className="grid gap-4 sm:grid-cols-2" onSubmit={onSubmit}>
        <label className="space-y-1.5 text-sm text-ink sm:col-span-2">
          <span className="font-medium">Domain</span>
          <select
            className="aegis-input font-mono"
            value={selectedDomainId ?? ""}
            onChange={(event) => {
              const nextId = Number(event.target.value);
              setDomainId(Number.isFinite(nextId) && nextId > 0 ? nextId : null);
            }}
            disabled={state === "loading" || state === "awaiting_approval"}
            required
          >
            {sortedDomains.map((domain) => (
              <option key={domain.id} value={domain.id}>
                {domain.domain}
              </option>
            ))}
          </select>
        </label>

        <div className="rounded-md border border-line bg-neutral-soft px-3 py-3 text-sm text-ink-muted sm:col-span-2">
          <p className="font-medium text-ink">Fixed DEMO mandate coverage</p>
          <p className="mt-1 font-mono text-xs">
            {defaultMerchantName} · {defaultMerchantUrl} · {defaultMerchantCountry} · {"$"}
            {defaultCapAmount.toFixed(2)} {defaultCurrency} · yearly
          </p>
        </div>

        <div className="flex flex-wrap gap-2.5 sm:col-span-2">
          <button
            type="submit"
            className="aegis-btn aegis-btn-primary"
            disabled={
              state === "loading" ||
              state === "awaiting_approval" ||
              !sortedDomains.length ||
              selectedDomainId === null
            }
          >
            {state === "loading" ? "Creating mandate session…" : "Start passkey approval"}
          </button>

          {state === "awaiting_approval" ? (
            <>
              <button
                type="button"
                className="aegis-btn aegis-btn-primary"
                onClick={onReconcile}
              >
                I approved it—sync mandate
              </button>
              <button type="button" className="aegis-btn aegis-btn-secondary" onClick={onCancel}>
                Cancel
              </button>
              <button type="button" className="aegis-btn aegis-btn-secondary" onClick={onMarkExpired}>
                Session expired
              </button>
              {approvalUrl ? (
                <button
                  type="button"
                  className="aegis-btn aegis-btn-accent"
                  onClick={() => openApprovalUrl(approvalUrl)}
                >
                  Reopen approval
                </button>
              ) : null}
            </>
          ) : null}

          {state === "cancelled" || state === "error" || state === "expired" ? (
            <button type="button" className="aegis-btn aegis-btn-secondary" onClick={onReset}>
              Try again
            </button>
          ) : null}
        </div>
      </form>

      {state === "loading" ? (
        <p role="status" className="text-sm text-ink-muted">
          Creating a Prava mandate-setup session…
        </p>
      ) : null}

      {state === "syncing" ? (
        <p role="status" className="text-sm text-ink-muted">
          Verifying the approved mandate with Prava…
        </p>
      ) : null}

      {state === "awaiting_approval" ? (
        <p
          role="status"
          className="rounded-lg border border-accent/20 bg-accent-soft px-3 py-2 text-sm text-accent"
        >
          Approve with your passkey in the Prava window. Use the team sandbox card when prompted.
        </p>
      ) : null}

      {state === "cancelled" ? (
        <p role="status" className="text-sm text-ink-muted">
          Mandate approval cancelled. No mandate id or payment credential was stored in the browser.
        </p>
      ) : null}

      {state === "active" ? (
        <p
          role="status"
          className="rounded-lg border border-ok/25 bg-ok-soft px-3 py-2 text-sm text-ok"
        >
          Active mandate synced. Next: run OpenAI ranking. Autonomous charge only fires on a final
          auto_renew with this coverage.
        </p>
      ) : null}

      {state === "expired" || state === "error" ? (
        <p role="alert" className="rounded-lg border border-danger/20 bg-danger-soft px-3 py-2 text-sm text-danger">
          {errorMessage}
        </p>
      ) : null}
    </section>
  );
}
